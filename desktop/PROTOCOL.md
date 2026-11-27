# Startup progress protocol

How a client (the desktop app, or anything else that wants a loading screen)
follows `./start.sh` and knows when Guaardvark is up.

The writer is `scripts/lib/start_progress.sh`, called from `start.sh`. Change
the two together with this file.

## Turning it on

```
./start.sh --no-browser --progress-file /path/to/progress.jsonl
```

`--progress-file=PATH` and the environment variable `GUAARDVARK_PROGRESS_FILE=PATH`
do the same; the flag wins. Without either, `start.sh` writes no progress file
and prints exactly what it always has.

- Use a new path for every launch. Two `start.sh` runs must never share a file.
- A second `start.sh` for an install started while one is still starting it
  refuses: it exits 1 at once, leaves the first one running, and writes
  `failed` with the label "Guaardvark is already starting" and the first one's
  PID in `detail`. A `start.sh` run after `ready` restarts Guaardvark.
- A relative path is taken from the directory `start.sh` was run in.
- The file is created (mode 600) or emptied as soon as the options are read,
  before anything else happens. Its directory is created if missing.
- `start.sh` does not open a browser unless `--app-mode` or
  `GUAARDVARK_APP_MODE` asks for one. A client that shows its own window passes
  `--no-browser` so neither can.
- `start.sh` asks for a `sudo` password on a terminal the first time it installs
  system packages. A client with no terminal needs passwordless `sudo` for that
  first run, or a root-only machine with `GUAARDVARK_ALLOW_ROOT=1`.

## The file

JSON Lines: one JSON object per line, UTF-8, appended with a single write per
line, so a reader that splits on `\n` never sees half an object. Read it by
following the file as it grows (`tail -f`, or re-reading from the last offset).

| Field    | Type   | Present              | Meaning |
|----------|--------|----------------------|---------|
| `t`      | number | always               | Unix time in seconds, with a fraction |
| `step`   | string | always               | Step id from the table below, or `ready` / `failed` |
| `state`  | string | always               | `start`, `ok`, `skip`, `fail` or `info` |
| `label`  | string | always               | Short text for the loading screen |
| `detail` | string | sometimes            | Longer text: what is being installed, why a step was skipped, the error |
| `pct`    | number | on `start` and `ready` | Rough overall position, 0 to 100 |

Ignore fields you do not know. Show a step id you do not know by its `label`.

### States

- `start`: the step began. It is the current step until its next line.
- `info`: the current step is doing something worth showing (`detail` says what).
  A step can send several.
- `ok`: the step finished.
- `skip`: the step did not need to run, or could not and startup went on without
  it (`detail` says which). Not an error.
- `fail`: the step had a problem. If the `failed` line follows, startup stopped
  here. If it does not, startup carried on (for example a failed interface build
  when an earlier build can still be served).

Steps do not overlap: a step's `ok`, `skip` or `fail` line comes before the next
step's `start`.

### Steps, in the order a start runs them

| `step`            | `label`                               | `pct` | What happens |
|-------------------|---------------------------------------|-------|--------------|
| `preflight`       | Checking this computer                | 2     | Single-run lock, platform detection, settings, freeing the ports, stopping servers left from an earlier run |
| `system_packages` | Checking system packages              | 5     | Python 3.12, Node.js, Ollama, FFmpeg; installed on a first run (`info`: "Installing Ollama", ...) |
| `redis`           | Starting Redis                        | 10    | Installs Redis on a first run, starts it, checks it answers |
| `postgres`        | Starting the database                 | 14    | Installs PostgreSQL on a first run, creates the user and database, starts it |
| `python_env`      | Setting up the Python environment     | 18    | Creates the virtual environment; on a first run installs the Python packages and PyTorch (`info` lines); installs the `guaardvark` command |
| `ollama`          | Starting Ollama                       | 45    | Starts the Ollama service. `skip` when Ollama is missing, turned off, or optional and not answering |
| `models`          | Checking AI models                    | 50    | Downloads a chat model and an embedding model if none is installed (`info`: "Downloading the chat model ..."). `skip` when downloads are turned off or Ollama is not running |
| `voice`           | Checking voice tools                  | 58    | Checks or builds Whisper.cpp. `skip` when the voice check is off |
| `workspace`       | Preparing the workspace               | 59    | Clears stale Python bytecode, refreshes the hardware profile |
| `node_env`        | Preparing the interface's packages    | 60    | `npm ci` when the interface's packages are missing or out of date (`info`) |
| `checks`          | Checking the installation             | 66    | Imports the backend once and runs the preflight check |
| `migrations`      | Updating the database                 | 72    | Brings the database schema up to date. `skip` when already current |
| `backend`         | Starting the backend                  | 76    | Starts the API server and waits for its port |
| `celery`          | Starting the background workers       | 84    | Starts the task workers |
| `frontend_build`  | Building the interface                | 88    | Production build of the interface. `skip` when nothing changed since the last build |
| `frontend`        | Starting the interface                | 93    | Starts the web server and waits for its port |
| `health`          | Checking that everything answers      | 96    | Backend, interface and worker health checks |
| `plugins`         | Starting plugins                      | 98    | Starts the plugins set to start with Guaardvark |

Notes for a client that draws a fixed list of rows:

- `frontend_build` can appear a second time, early (during `python_env`), with
  no `pct`, when an older build exists and the source is newer.
- After the backend fails to start, `start.sh` can undo a change Guaardvark made
  to its own code and run itself once more. The second run continues the same
  file and starts again from `preflight`, so step ids repeat and `pct` goes back.
- A step never reached before the terminal line simply has no lines.

### Terminal lines

Exactly one of these is the last line `start.sh` writes. Nothing follows it.

Ready:

```json
{"t":1795810411.59,"step":"ready","state":"ok","label":"Guaardvark is ready","pct":100,"url":"http://localhost:5173","api":"http://localhost:5000"}
```

`url` is the web interface and `api` the API server, on the ports this run
used. `ready` is written only after both `GET <api>/api/health` and `GET <url>/`
have answered with a 2xx, so the client can load `url` at once. `start.sh` keeps
running for a few more seconds after `ready` (closing notes, an advisory GPU
check) and then exits 0.

Failed:

```json
{"t":1795810411.56,"step":"failed","state":"fail","label":"Startup stopped at: Starting the database","detail":"PostgreSQL setup failed","log":"<install>/logs/setup.log","at":"postgres"}
```

- `label`: one line for the error screen.
- `detail`: what `start.sh` reported as the error, or how it ended if it
  reported nothing. Always present, may be empty.
- `log`: absolute path of the log to show (the example abbreviates the install directory). Empty when startup stopped before any
  log existed (for example another `start.sh` already running, or a root user).
- `at`: the step that was running. Absent when no step was open.

`failed` is written on every way out of `start.sh` that did not reach `ready`:
an error exit, an option that ends early (`--wsl-keep-alive`), a CI environment,
Ctrl+C and `SIGTERM` (including the one `./stop.sh` sends to a `start.sh` that is
still running). Two cases write nothing, so a client must also watch the
`start.sh` process: **if the process has exited and the file has no terminal
line, treat it as failed.**

- `SIGKILL`. `./stop.sh` follows its `SIGTERM` with `SIGKILL` 0.3 seconds later,
  which is normally long enough for the line to be written, but not guaranteed.
- `--help`, which prints and exits before the file is touched.

Keep `start.sh`'s own output as well (redirect it to a file). Some errors, a
Python traceback for one, are printed there and are in no log; `detail` carries
only their last line.

`failed` can also be written while `start.sh` exits 0: the services were
launched but the backend or the interface did not answer at the end. Parts of
Guaardvark may then be running. Run `./stop.sh` before starting again.

## Ports

| What          | Default | macOS default | Set with |
|---------------|---------|---------------|----------|
| API (`api`)   | 5000    | 5055          | `FLASK_PORT` in the install's `.env` or the environment |
| Web (`url`)   | 5173    | 5173          | `VITE_PORT` in the install's `.env` or the environment |

Before a start, read `FLASK_PORT` and `VITE_PORT` from `<install>/.env` and fall
back to the defaults. After a start, use the `url` and `api` of the `ready` line.
A running instance also records its ports in `~/.guaardvark/runtime.json`
(`root`, `backend_port`, `frontend_port`, `backend_pid`, `started_at`);
`./stop.sh` removes that file.

## Is it already running?

Check before calling `start.sh`, because `start.sh` always restarts: its first
step runs `./stop.sh` on the install it belongs to, which ends any generation in
progress.

1. `GET <api>/api/health` answers 200 with JSON whose `status` is `"ok"`.
2. `GET <url>/` answers 200.

Both true: Guaardvark is running; open `url` and do not call `start.sh`. A
client that did not start the instance should not stop it on quit without
asking.

## Stopping

`./stop.sh` (no options) stops what `start.sh` started for this install: the
backend, the workers, the interface server, plugins, ComfyUI, and Ollama only if
`start.sh` launched it. It also ends a `start.sh` that is still running. It
exits 0 whether or not anything was running: about 15 seconds when it has
services to stop, under a second when it has none.

It leaves running: PostgreSQL and the system Redis service (they are system
services shared with nothing else here, and the next start reuses them), an
Ollama that was already running before `start.sh`, and anything belonging to
another Guaardvark install on the same machine.
