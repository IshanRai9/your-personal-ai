# Your Personal AI

A Windows desktop assistant for local English voice and text commands. It opens apps, runs configured scripts and workflows, and confirms shutdown or restart requests.

## Run

Open **Your Personal AI** from the Windows Start Menu. It runs in the notification area beside the clock. Use the hidden-icons arrow if Windows places its icon there.

- Closing the window hides it to the tray and keeps wake listening available.
- Right-click the tray icon to open the window, change wake listening, configure startup, or **Quit**.
- **Start with Windows** launches it quietly when you sign in. You can also change this in **Settings → General**.
- Opening the app again brings the running instance forward.
- Task Manager can end **YourPersonalAI.exe**. The app's model processes close with it.

Local model setup is available through **Settings → General → Download / repair local models**.

### Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run these commands in this folder.

```powershell
uv sync
uv run personal-ai --setup
uv run personal-ai
```

Setup downloads the portable Ollama runtime, `qwen3:4b-instruct`, and the English Whisper small model. Allow several GB of disk space and an internet connection for setup. You can also download or repair models from **Settings → General**.

Build the standalone Windows app with `uv run python scripts/build_windows.py`. The executable is `dist/YourPersonalAI/YourPersonalAI.exe`. Keep its companion `_internal` folder with it. The packaged app runs without a terminal or a separate Python installation.

The assistant uses a dedicated Ollama process at `127.0.0.1:11435`, with cloud features disabled. It closes that process when you exit. Your existing Ollama installation can supply the executable, while this assistant keeps its model files in its own data folder.

## Commands

- Type a request such as `open Notepad` and press Enter.
- Hold **Hold to speak** while speaking, then release to run the recognized command.
- Enable **Wake listening**, say `Hey Assistant`, wait for the tone, then speak your command. Recording finishes after about 0.8 seconds without speech. If you say nothing for three seconds, it returns to wake listening. Commands have a ten-second recording limit.
- Double-click an entry in the command list to run it directly.
- Say `switch to Chrome` to bring its most recent open window forward. Use `minimize this`, `snap this window left`, or `snap this window right` on the window you were using before activating the assistant.
- Say or type `close active tab` (also `close this tab`). This targets the window you were using before opening the assistant. Supported apps: Chrome, Edge, Firefox, Brave, Opera, Vivaldi, Arc, Zen, VS Code, Notepad, Notepad++, Sublime Text, and File Explorer. It requests one tab close and leaves save/leave-page prompts in the app. Closing the last tab may also close that app's window. If you switch to a different window while the command is being understood, the assistant asks you to try again.
- Shutdown and restart require confirmation. App launches and configured scripts run immediately.
- Apps that require administrator access open the Windows UAC prompt. Approve it to launch the app, or cancel to stop the command and its remaining steps.
- Turn optional spoken replies on in **Settings → General**.

Change the wake phrase in **Settings → General**. Wake detection uses a small local Vosk model, installed by setup. It works while the assistant is running, including when its window is hidden in the tray. The wake toggle turns background microphone listening on or off. Listening pauses during commands, settings, and spoken replies. Quitting the assistant stops listening.

Apps are discovered from Windows Start Menu shortcuts. Enable or hide entries under **Settings → Apps**. Add other programs through custom commands.

## Custom commands

Open **Settings → Custom commands → New command**. Give the command a name, description, and example phrases, then add steps in the desired order.

Each step can open an app or run a script and wait for its exit code. Use **Choose a Python or PowerShell script** to fill in the interpreter and script path. You can also configure an executable or interpreter, arguments, and a working directory yourself. Enter one argument per line, with paths entered without surrounding quotes.

For example, a `Start work` command could open your editor, open a browser, and run a local preparation script. A failed script stops subsequent steps and displays its exit code and output. Cancellation stops a running script process tree. Completed steps have already taken effect.

The development app uses its Python environment for Python scripts by default. The packaged app asks you to choose an installed Python interpreter when adding a Python script. Choose your script's own interpreter when it needs separate dependencies. PowerShell scripts follow your existing Windows execution policy.

## Local data

Settings and models live under `%LOCALAPPDATA%\YourPersonalAI`. Settings are saved atomically as `settings.json`. Recent activity stays in memory for the current session. Wake listening processes small audio chunks in memory. Command recordings are held in memory and discarded after processing. Spoken replies use an installed Windows speech voice.

Assistant inference and speech recognition operate offline after setup. Programs and scripts you configure retain their own behavior, including any network access they perform.

Set `YPA_DATA_DIR` to use a different local data directory. The application permits one instance per data directory. The dedicated runtime port permits one active model service at a time.

## Verify

```powershell
uv run python -m unittest discover -s tests -v
uv run ruff check .
```

Power actions are intercepted by the verification suite. To check speech manually, hold the microphone button and say `open Notepad`, then confirm that the visible transcript and launched app match your request.
