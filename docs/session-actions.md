# Session actions

Select an agent, then use **Open** or **Delete** beside the Agent details heading.
Sample agents in demo mode do not support these actions.

## Open a chat

Open displays the command for resuming the selected chat, with a Copy button.
When an application link is available, use it to open the original app. If the
URI handler is missing or the app does not open, paste the command into your
terminal. The command includes the configured provider profile and recorded
working directory. Overwatch never runs the command automatically.

Codex uses its [documented thread links](https://learn.chatgpt.com/docs/app/commands)
and `codex resume`. Claude Code uses its
[documented resume command](https://code.claude.com/docs/en/cli-reference).
Claude subagent transcripts reopen their parent conversation.

## Delete local transcripts

Delete first checks the session and shows a confirmation. It removes the chat's
transcripts, duplicate transcript copies, and recorded descendants from the
provider's observed session directory, including transcripts beyond the
dashboard's file limit. Codex's separate archived-session directory is outside
this action. Delete does not remove project files or edit provider indexes,
databases, account history, or cloud copies. A provider's own history list may
retain an entry after its local transcript is removed.

Chats with an open recorded turn, including stale turns, cannot be deleted.
Finish or stop them in the original app first. This check is based on logs;
it cannot prove process liveness. A session that changes after confirmation
must be reviewed again.

## Recovery and filesystem access

Removed files are moved into `<provider-home>/.overwatch-trash/<batch-id>/`.
Each batch contains a `manifest.json` mapping the stored files to their original
paths. To recover a chat, stop its provider, inspect the manifest, and move the
transcripts back under `sessions/` for Codex or `projects/` for Claude. Preserve
any files already at those paths. Recovery copies are retained until you
remove them manually.

Deletion requires writable local session directories and filesystem support for
safe directory-relative operations. Docker's default session mounts are
read-only; run Overwatch locally to delete chats. URI handlers belong to the
machine running the browser, so the copyable command remains available even
when an application link is offered.
