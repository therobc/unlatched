// Spawns the command-line tool as a child process and streams its output
// back without blocking the UI thread. Reader threads own the pipes and
// push lines through a channel; the UI polls the channel once per frame.

use std::io::{BufRead, BufReader};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

const DONE_MARKER: &str = "__unlatched_process_done__:";

/// Windows CREATE_NO_WINDOW. Declared here rather than pulling in winapi for
/// one constant.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// How often the waiter asks whether the child has exited.
///
/// A blocking `wait()` would need `&mut Child` for its whole duration, so
/// nothing else could ever reach the child to stop it - which is how the engine
/// came to outlive the window that started it. Polling holds the lock for
/// microseconds instead. 100ms matches the repaint the UI already schedules
/// while a process runs, so this adds no perceptible latency to "it finished".
const WAIT_POLL: Duration = Duration::from_millis(100);

/// How long the waiter gives the pipe readers to finish after the child has
/// exited, before it reports completion anyway.
///
/// THE MARKER USED TO RACE THE OUTPUT. The waiter and the two reader threads
/// are independent, so "the child exited" could reach the UI before the last
/// lines the child printed - and the UI drops the process the same frame it
/// sees `finished`, so those lines were simply lost. The engine prints its
/// answer LAST: the reason an add failed, the summary a collect ends with.
/// Losing that is losing exactly the line somebody needed.
///
/// Bounded rather than a plain join, so a reader that never reaches EOF - a
/// grandchild holding the pipe open - delays the completion notice by a
/// second instead of withholding it forever.
///
/// BELIEVED, NOT MEASURED, and the distinction matters here. The race is real
/// by construction - three threads, no ordering between them - but it could
/// not be reproduced from outside: the waiter's first `try_wait` finds the
/// child still starting, so by its next look 100ms later the readers have
/// flushed. The test below asserts the CONTRACT and passes without this
/// drain, which is stated there too. What is verified is that the drain costs
/// nothing when the readers are already done; what is not is that anybody has
/// seen the marker actually overtake them.
const DRAIN_GRACE: Duration = Duration::from_millis(1_000);

pub struct RunningProcess {
    /// Shared so the waiter can poll it and `kill` can reach it. Before this,
    /// the child was MOVED into the waiting thread and no handle survived, so
    /// closing the app left a collect running with no window, still writing to
    /// the database - and a newly opened app, seeing no running process of its
    /// own, would start a second engine against the same file.
    child: Arc<Mutex<Child>>,
    receiver: Receiver<String>,
    pub finished: bool,
    pub exit_code: Option<i32>,
}

impl RunningProcess {
    pub fn spawn(program: &str, args: &[String]) -> Result<Self, String> {
        let mut cmd = Command::new(program);
        cmd.args(args);
        // No console window. The engine is a frozen console executable, so
        // without this every collect, screen and discover flashes a black
        // window over whatever the person is doing - the single loudest
        // "this is a script, not an app" tell there is. Output still reaches
        // the UI: it comes through the pipes below, not a terminal.
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd.stdin(Stdio::null());
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("failed to start '{program}': {e}"))?;

        let (tx, rx) = mpsc::channel::<String>();

        // Counted so the waiter can tell whether every line has been sent
        // before it announces that the run is over. See DRAIN_GRACE.
        let readers = Arc::new(AtomicUsize::new(0));

        if let Some(out) = child.stdout.take() {
            let tx_out = tx.clone();
            let done = Arc::clone(&readers);
            readers.fetch_add(1, Ordering::SeqCst);
            thread::spawn(move || {
                for line in BufReader::new(out).lines() {
                    match line {
                        Ok(l) => {
                            if tx_out.send(l).is_err() {
                                break;
                            }
                        }
                        Err(_) => break,
                    }
                }
                done.fetch_sub(1, Ordering::SeqCst);
            });
        }

        if let Some(err) = child.stderr.take() {
            let tx_err = tx.clone();
            let done = Arc::clone(&readers);
            readers.fetch_add(1, Ordering::SeqCst);
            thread::spawn(move || {
                for line in BufReader::new(err).lines() {
                    match line {
                        Ok(l) => {
                            if tx_err.send(format!("[stderr] {l}")).is_err() {
                                break;
                            }
                        }
                        Err(_) => break,
                    }
                }
                done.fetch_sub(1, Ordering::SeqCst);
            });
        }

        let child = Arc::new(Mutex::new(child));
        let waiter = Arc::clone(&child);
        let readers = Arc::clone(&readers);
        thread::spawn(move || {
            loop {
                let status = match waiter.lock() {
                    Ok(mut guard) => guard.try_wait(),
                    // The mutex is poisoned only if a holder panicked. Nothing
                    // useful is left to wait on, so stop rather than spin.
                    Err(_) => break,
                };
                match status {
                    Ok(Some(exit)) => {
                        let code = exit.code().unwrap_or(-1);
                        // EVERY LINE FIRST, THEN THE MARKER. The UI drops the
                        // process on the frame it sees the marker, so anything
                        // a reader had not sent yet would never be shown - and
                        // the engine's last line is the one that says what
                        // happened.
                        let deadline = std::time::Instant::now() + DRAIN_GRACE;
                        while readers.load(Ordering::SeqCst) > 0
                            && std::time::Instant::now() < deadline
                        {
                            thread::sleep(Duration::from_millis(5));
                        }
                        let _ = tx.send(format!("{DONE_MARKER}{code}"));
                        break;
                    }
                    Ok(None) => thread::sleep(WAIT_POLL),
                    Err(_) => {
                        let _ = tx.send(format!("{DONE_MARKER}-1"));
                        break;
                    }
                }
            }
        });

        Ok(RunningProcess {
            child,
            receiver: rx,
            finished: false,
            exit_code: None,
        })
    }

    /// Stops the child if it is still running. Safe to call more than once.
    pub fn kill(&mut self) {
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
            // Reaped so the process does not linger as a zombie on unix. The
            // result is ignored: it has already exited in the common case, and
            // there is nothing to do about a failure at this point anyway.
            let _ = child.wait();
        }
    }
}

impl Drop for RunningProcess {
    /// The engine does not outlive the window that started it.
    ///
    /// Without this the child was orphaned on close: a collect kept running
    /// with no UI anywhere, still writing to the database, and a newly opened
    /// app had no knowledge of it. That second app, seeing no running process
    /// of its own, would start ANOTHER engine against the same file.
    ///
    /// The engine also takes a cross-process lock now, so a second one is
    /// refused rather than corrupting anything - but refusing to collect is a
    /// poor substitute for not leaking the first process.
    fn drop(&mut self) {
        self.kill();
    }
}

impl RunningProcess {
    /// Drains everything currently buffered in the channel. Call once per
    /// frame; returns the plain log lines received since the last poll.
    ///
    /// The done-marker is only sent after the readers have drained, so every
    /// line the child printed is already ahead of it in this channel.
    pub fn poll(&mut self) -> Vec<String> {
        let mut lines = Vec::new();
        while let Ok(line) = self.receiver.try_recv() {
            if let Some(code_str) = line.strip_prefix(DONE_MARKER) {
                self.finished = true;
                self.exit_code = code_str.parse().ok();
            } else {
                lines.push(line);
            }
        }
        lines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A real child, run to completion, whose LAST line has to survive.
    ///
    /// The engine prints its answer last - the reason an add failed, the
    /// summary a collect ends with - and the UI drops the process on the
    /// frame it sees `finished`. So a done-marker that overtakes the pipe
    /// readers does not merely reorder the log: it deletes the one line
    /// somebody was waiting for.
    ///
    /// WHAT THIS PROVES AND WHAT IT DOES NOT, stated plainly because the
    /// distinction is easy to lose. It asserts the CONTRACT - every line
    /// reaches the caller ahead of the marker - and it PASSES with the drain
    /// in `spawn` removed, so it is NOT a positive control and must not be
    /// read as one. The race could not be reproduced from outside: the
    /// waiter's first `try_wait` finds the child still starting, so by the
    /// time it looks again 100ms later the readers have long since flushed.
    /// The window is real - three independent threads with no ordering
    /// between them - and narrow, and the drain closes it by construction
    /// rather than by relying on that timing continuing to hold.
    #[test]
    fn the_last_line_a_child_prints_is_never_lost() {
        let (finished, code, seen) = run_to_completion(&[
            "-c".to_string(),
            "print('working'); print('LAST-LINE')".to_string(),
        ]);
        assert!(finished, "the child never reported finishing");
        assert_eq!(code, Some(0));
        assert!(
            seen.iter().any(|l| l.contains("LAST-LINE")),
            "the final line was lost - got {seen:?}"
        );
    }

    /// The exit code comes back for a child that fails, and the reason it
    /// printed comes with it - which is exactly what app::report_add_job
    /// reads back to explain a failed add.
    #[test]
    fn a_failing_child_reports_its_code_and_its_reason() {
        let (_, code, seen) = run_to_completion(&[
            "-c".to_string(),
            "import sys; print('why it failed'); sys.exit(3)".to_string(),
        ]);
        assert_eq!(code, Some(3));
        assert!(seen.iter().any(|l| l.contains("why it failed")), "{seen:?}");
    }

    /// Anything a child writes to stderr is kept and marked, rather than
    /// dropped: a traceback is the most useful thing the engine ever prints.
    #[test]
    fn stderr_is_kept_and_marked() {
        let (_, _, seen) = run_to_completion(&[
            "-c".to_string(),
            "import sys; print('bad thing', file=sys.stderr)".to_string(),
        ]);
        assert!(
            seen.iter().any(|l| l.starts_with("[stderr] ") && l.contains("bad thing")),
            "{seen:?}"
        );
    }

    /// Drives a child the way the UI does: poll each frame, then one final
    /// drain on the frame `finished` appears - and nothing after it, because
    /// that is the frame the UI drops the process on.
    ///
    /// Python is the helper because the engine IS Python and the gates run
    /// pytest before they reach here, so a machine that can run this suite
    /// has it.
    fn run_to_completion(args: &[String]) -> (bool, Option<i32>, Vec<String>) {
        let mut proc = RunningProcess::spawn("python", args).expect("spawn python");
        let mut seen: Vec<String> = Vec::new();
        let started = std::time::Instant::now();
        while !proc.finished && started.elapsed() < Duration::from_secs(20) {
            seen.extend(proc.poll());
            thread::sleep(Duration::from_millis(2));
        }
        seen.extend(proc.poll());
        (proc.finished, proc.exit_code, seen)
    }
}
