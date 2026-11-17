# Your Personal AI

A Windows desktop assistant for local English voice and text commands. It opens apps, runs configured scripts and workflows, and confirms shutdown or restart requests.

## Run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run these commands in this folder.

```powershell
uv sync
uv run personal-ai --setup
uv run personal-ai
```

Setup downloads the portable Ollama runtime, `qwen3:4b-instruct`, and the English Whisper small model. Allow several GB of disk space and an internet connection for setup. You can also download or repair models from **Settings → General**.

The assistant uses a dedicated Ollama process at `127.0.0.1:11435`, with cloud features disabled. It closes that process when you exit. Your existing Ollama installation can supply the executable, while this assistant keeps its model files in its own data folder.

## Commands

- Type a request such as `open Notepad` and press Enter.
- Hold **Hold to speak** while speaking, then release to run the recognized command.
- Double-click an entry in the command list to run it directly.
- Shutdown and restart require confirmation. App launches and configured scripts run immediately.
- Turn optional spoken replies on in **Settings → General**.

Apps are discovered from Windows Start Menu shortcuts. Enable or hide entries under **Settings → Apps**. Add other programs through custom commands.

## Custom commands

Open **Settings → Custom commands → New command**. Give the command a name, description, and example phrases, then add steps in the desired order.

Each step can open an app or run a script and wait for its exit code. Use **Choose a Python or PowerShell script** to fill in the interpreter and script path. You can also configure an executable or interpreter, arguments, and a working directory yourself. Enter one argument per line, with paths entered without surrounding quotes.

For example, a `Start work` command could open your editor, open a browser, and run a local preparation script. A failed script stops subsequent steps and displays its exit code and output. Cancellation stops a running script process tree. Completed steps have already taken effect.

Python scripts use the assistant's Python environment by default. Choose your script's own interpreter when it needs separate dependencies. PowerShell scripts follow your existing Windows execution policy.

## Local data

Settings and models live under `%LOCALAPPDATA%\YourPersonalAI`. Settings are saved atomically as `settings.json`. Recent activity stays in memory for the current session. Microphone audio is held in memory and discarded after processing. Spoken replies use an installed Windows speech voice.

Assistant inference and speech recognition operate offline after setup. Programs and scripts you configure retain their own behavior, including any network access they perform.

Set `YPA_DATA_DIR` to use a different local data directory. The application permits one instance per data directory. The dedicated runtime port permits one active model service at a time.

## Verify

```powershell
uv run python -m unittest discover -s tests -v
uv run ruff check .
```

Power actions are intercepted by the verification suite. To check speech manually, hold the microphone button and say `open Notepad`, then confirm that the visible transcript and launched app match your request.
