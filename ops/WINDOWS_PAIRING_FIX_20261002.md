# Windows AppLens connection incident

## Observed failure

The installed collector attempted to read WorkBuddy's active log with
`File.ReadLines`. Windows reported a sharing violation. The foreground collector
exited, and its connection stopped. The GUI retained the exited process object,
so another click could return without starting a new connection.

## Fix

- Read active logs with ReadWrite/Delete sharing; tolerate temporarily exclusive
  locks so independent context uploads can proceed.
- Send a heartbeat before collection and maintain it in a separate background job.
- Persist a connection receipt independently of context collection state.
- Dispose an exited collector before retrying; reuse the saved DPAPI identity on
  startup without requiring another one-time pairing code.
- Show heartbeat freshness in the client, even before any context is collected.

## Verified on the existing Windows test machine

The repaired GUI ran in the logged-in desktop session. The server returned
`online: true` for device `d8e0c08d905ea5493c313a01` on two observations, with an
advancing heartbeat timestamp. The server contained 31 model-context records;
the user also confirmed the records were visible. This count does not prove
that every record contains a complete HTTP request body.

The authorized test lease `382797000f92e42b693e59f1` was then released at the
user's request. Provider queries returned zero matching hosts and zero matching
public IPs. No replacement machine was created.

## Build regression coverage

Device tests cover independent heartbeat, preserved inventory and revoked-token
rejection. Windows context tests hold a log with an exclusive lock and verify
that an independent network-context record still uploads. CI also parses the
PowerShell scripts, compiles the GUI, installs the package and starts the GUI.
