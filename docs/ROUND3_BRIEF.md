# ARENA: FINAL KITSUNE-STYLE EXPERIENCE

Preserve the existing working autonomous build engine, conversational director, NXT Blue appearance, authentication and Tailscale access.

The product should be radically simple: **I tell Arena what I want, it coordinates the coding agents, and I can see what's happening without managing engineering infrastructure.**

## 1. Home: only what matters

The home screen should show:

- One conversational input for speaking or typing to Arena.
- One compact row per actively building project, such as "Building Continuoum".
- A small, collapsible section for completed or paused work.
- One dropdown menu for everything else.

No permanent bottom navigation, independent Agents tab, technical dashboards or large collections of status cards.

## 2. Build detail: Kitsune-style story blocks

Tapping "Building Continuoum" opens the current build.

Display its real, simultaneous engineering workstreams as connected story blocks.

Each block should explain:

- What component is being built.
- Why it matters.
- How it connects to other components.
- Which Claude or Codex agent owns it.
- What that agent is doing now.
- Whether the work is running, waiting, completed or blocked.

Tapping a block opens a plain-English explanation of its architecture, progress, recent decisions and failures.

Do not display source code, terminal output, Git hashes or filenames by default. Keep technical evidence accessible under Advanced.

Never fabricate progress or agent activity.

## 3. Archive finished work with checkboxes

I want to clear completed work without deleting anything valuable.

- Allow me to tick individual completed builds or projects.
- Provide Select All and Archive Selected actions.
- Archived items disappear from the normal home view.
- Keep all underlying Git history, commits, transcripts and verification evidence intact.
- Allow me to view and restore archived entries from the secondary menu.
- Make archiving reversible and separate from destructive deletion.

Do not archive an actively running build merely because I selected it.

## 4. Discover ALL active development work on my Mac

This is critical.

When I ask Arena:

**"What's running on my laptop?"**

It must not merely repeat the status stored in Arena's database.

It should perform a fresh, read-only discovery of the MacBook Air development environment, using available operating-system information.

Identify, where possible:

- Running Claude Code sessions and subagents.
- Running Codex sessions and subagents.
- Active Arena-managed builds.
- Other coding-agent processes.
- Development servers, test runners and related background services.
- Active development worktrees and associated repositories.
- The repository or working directory associated with each identifiable process.

Reconcile this information with Arena's existing task database.

Distinguish clearly between:

**Managed by Arena:** Work Arena launched and can control.

**Discovered externally:** Work running independently in Terminal, Claude Code, Codex or another development environment.

**Unknown or unverified:** Processes whose actual activity cannot be established reliably.

For each identified activity, give me a concise explanation:

"Claude is working on LeoOS."

"Codex is running tests for Continuoum."

"Arena has two active builds."

"An independent development server is running for Agent Exchange."

These are illustrative examples, not fabricated statuses.

Only report a current task or repository when supported by actual process, session or workspace evidence. Process existence alone does not prove useful progress.

Discovery must be read-only. Do not terminate, attach to, modify or take control of independent sessions without explicit authorisation.

Do not inspect unrelated personal applications, capture credentials or introduce a heavyweight machine-monitoring framework.

If macOS permissions prevent accurate discovery, explain the limitation rather than claiming complete visibility.

## 5. Conversational director

The director must support natural questions such as:

- "What's running right now?"
- "What is Claude doing?"
- "What's happening with Continuoum?"
- "What finished overnight?"
- "Which agents are stuck?"
- "What went wrong with that build?"
- "Can you investigate that failure?"
- "Let's start improving LeoOS."

Only an explicit request to execute should launch a new build.

Arena should infer context where possible and ask me only about genuine product ambiguity or authorisation.

## 6. Plain-English failure summaries

When a build fails, don't present 200 lines of Python errors.

Explain:

- What failed.
- What it was supposed to accomplish.
- Whether the failure is recoverable.
- What the agents have already tried.
- What happens next.
- Whether I actually need to intervene.

Keep detailed evidence available only when requested.

## 7. Definition of done

Demonstrate the following on Android using the real running system:

1. Open Arena and see only currently relevant projects.
2. Tap "Building Continuoum" to inspect its actual parallel story blocks.
3. Tap a block and understand its architecture and current state in ordinary English.
4. Ask "What's running on my Mac?" and see both Arena-managed and independently discovered development activity, correctly distinguished.
5. Archive a completed build using a checkbox, then restore it.
6. Ask about a real failure and receive an accurate plain-English explanation.
7. Close Arena and return without interrupting ongoing builds.

Preserve the existing backend. Make the smallest changes necessary.

No new framework, no unrelated features and no another enormous review programme.

**I want a personal AI engineering director, not a developer operations console. Own the implementation and deliver the working experience.**