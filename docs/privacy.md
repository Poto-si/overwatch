# Privacy and boundaries

The server binds to **127.0.0.1** by default, restricts requests to local same-origin access,
and accepts no mutation endpoints. It never spawns, resumes, messages, or
controls agents, and does not modify either platform's logs or databases.

Activity includes event labels and tool names, without prompt text, reasoning,
tool arguments, credentials, or tool results. Thread titles and local paths can
still be sensitive, so keep the dashboard on your own machine. There is no CDN,
external font loading, analytics, or telemetry.
