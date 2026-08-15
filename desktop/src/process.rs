// Spawns the command-line tool as a child process and streams its output
// back without blocking the UI thread. Reader threads own the pipes and
// push lines through a channel; the UI polls the channel once per frame.

use std::io::{BufRead, BufReader};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::process::{Child, Command, Stdio};
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

        if let Some(out) = child.stdout.take() {
            let tx_out = tx.clone();
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
            });
        }

        if let Some(err) = child.stderr.take() {
            let tx_err = tx.clone();
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
            });
        }

        let child = Arc::new(Mutex::new(child));
        let waiter = Arc::clone(&child);
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
