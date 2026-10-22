# Privacy and boundaries

The server binds to **127.0.0.1** by default and restricts requests to local
same-origin access. Observation does not modify session logs or databases.
The explicit Delete action moves the selected chat's transcripts and recorded
subagents out of the session directory into local recovery storage, after
confirmation. Deletion requests require a matching Origin and JSON action
headers; clients cannot supply filesystem paths. Provider databases and project
files remain untouched. See [session actions](session-actions.md).

Open offers an application link when available and a resume command to copy.
Overwatch does not execute that command, send prompts, or call model providers.

Activity includes event labels and tool names, without prompt text, reasoning,
tool arguments, credentials, or tool results. Thread titles and local paths can
still be sensitive, so keep the dashboard on your own machine. There is no CDN,
external font loading, analytics, or telemetry.

Recovery copies contain the original transcripts, including any private contents
already stored by the provider. They stay on your machine under the configured
provider home and are never served by Overwatch's HTTP endpoints.
