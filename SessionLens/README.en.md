# SessionLens

SessionLens is a desktop assistant for understanding your Codex and WorkBuddy task history. It runs on macOS and Windows, collects session logs locally, and helps you explore projects, requests, recorded reasoning, tool inputs and results.

The assistant shows the answer first. Expand the evidence when you need to check it. Process playback follows recorded tool calls; it never runs those tools again. Missing evidence remains unknown.

## Using the app

Select **English** at the bottom of the sidebar. Your choice is saved on this device. Switching language keeps your question, draft, selected step and expanded evidence.

Ask questions such as:

- Which projects has WorkBuddy worked on?
- What tasks were completed in my project?
- Why was the code changed?
- How was this task completed? Which tools were used?
- How many user turns and tool calls did this task involve?

Choose a matching task if several records fit your question. Project and count queries use local records. Explanations may use your configured model endpoint. Original prompts, code, filenames, tool parameters and results retain their original language. The current logs do not establish the total number of model API requests.

## Local storage and collection

- macOS: `~/Library/Application Support/SessionLens`
- Windows: `%LOCALAPPDATA%\SessionLens`

`collector.db` contains collected records and source cursors. Separate local indexes support search and project associations; `conversations.db` stores assistant conversations. Settings are in `settings.json`.

Collection supports both Codex and WorkBuddy. Uploading requires a configured endpoint and device token. Without them, records stay on this device. A configured assistant model receives the relevant evidence used to answer a question. Local embedding search does not upload logs. The bundled embedding setup currently uses a Chinese BGE model; English query recognition is supported, but cross-language semantic retrieval has not passed a separate accuracy evaluation.

## Development

```sh
python -m pip install -r requirements-desktop.txt
python desktop_main.py
python -m unittest discover -s tests -v
python desktop_main.py --self-test
python -m PyInstaller --noconfirm --windowed --name SessionLens desktop_main.py
```

The same Qt components provide the assistant and playback on both platforms. GitHub Actions builds macOS and Windows packages and checks both interface languages. Packages are not yet signed or notarized.

See the [Chinese documentation](README.md) for detailed collection limits and architecture.
