# From a ChatGPT conversation to a running Arena build

Arena does the engineering; ChatGPT is where you think. This is the supported, verified
way to move a finished idea from one to the other, and why it is built this way.

## The planning stage (ChatGPT)

- Brainstorm however you like (ChatGPT voice is fine).
- Finalise with **GPT-6 at Extra High reasoning effort**. That is a setting in ChatGPT's
  model picker and it is yours to choose; Arena cannot and does not change it. Use
  **GPT-6 Pro only when you explicitly ask for it** — nothing in Arena ever escalates a
  conversation to Pro.
- End by asking ChatGPT for a build-ready specification. A prompt that works well:

  > Turn our discussion into a build specification for an autonomous coding system.
  > Sections: Objective (one paragraph of the outcome a user sees); Scope (which app or
  > repository, what must not change); Acceptance criteria (testable, numbered);
  > Constraints (security, data, compatibility). Plain language, no implementation
  > detail about frameworks or files.

  Arena's director reads the repository itself and decides the engineering; the spec
  only needs to say what the product should do and how to tell it is done.

## Handing the specification to Arena

**Android (installed Arena app):** in the ChatGPT app, long-press the specification →
Share → **Arena**. Arena opens with the text already in "Tell me what to build" and the
project set to *Let Arena decide*. Tap **Build this for me**. (This uses the Web Share
Target standard: the Arena PWA registers as a share destination; nothing leaves your
tailnet.) Install the app once from Chrome → menu → *Add to Home screen* while on the
private Arena address.

**Anywhere else (Mac, iPhone, browser):** copy the specification, open Arena, paste it
into "Tell me what to build", submit.

Arena then picks the authorised repository when the request makes it unambiguous (by
project name, repository name or enrolled keywords; "Arena", "your own dashboard" and
similar mean Arena itself). If two projects could fit, it asks you to tap one — nothing
starts until you do.

## Why there is no automatic ChatGPT → Arena link

ChatGPT's supported automation surfaces (Actions in custom GPTs, connectors/MCP servers)
can only call endpoints that are reachable from OpenAI's servers on the public internet.
Arena is deliberately reachable only inside your Tailscale network with device pairing,
and exposing a code-execution service publicly is out of bounds. There is also no
supported way for an outside application to read your private ChatGPT conversations,
select a model, or start a Pro session. Share-to-Arena and paste are therefore the
complete, supported handoff; both are verified below.

## What happens next (no action needed)

Director specification → Codex/Claude implementation in isolated workspaces → frozen
checks → independent cross-provider review → director sign-off → combined checks →
integration into the project's local `main` (automatic for projects enrolled with the
automatic policy) → for Arena itself, a new release is activated and health-checked,
rolled back automatically if it fails. You can close the app; progress and results stay
in the Build and Review screens, and genuine blockers are explained in plain English in
"Needs you".
