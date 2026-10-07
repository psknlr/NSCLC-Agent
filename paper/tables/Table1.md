**Table 1 | Components of the agent runtime**

| Concern | Pattern in mainstream agent runtimes | Implementation in NSCLC-Agent |
|---|---|---|
| Decision authority | The model decides; the harness checks | Full autonomy (default): the model decides stage, biomarker category, treatment intent and plan with 10 information tools; the 9 kernel decision tools are offered only in kernel-assisted mode; the kernel audits each consult independently afterwards |
| Tool abstraction | Typed tools; per-agent toolsets | JSON-schema tools with parallel-safety and terminal flags; each agent sees its own filtered toolset |
| Agent loop | One ReAct loop for every agent | Shared loop for the lead and specialists; concurrent read-only calls*; ordered writers; terminal tool last; history repaired on interruption |
| Planning | To-do / plan tool | update_plan: consult plan kept across turns and shown to the model each turn |
| Sub-agents | Agents as tools | delegate: 7 specialists, each with its own context, prompt and read-only allow-list; structured reports |
| Hooks | Prompt / post-tool / stop hooks | 8 deterministic, individually switchable hooks; 5 on in full autonomy (emergency and provenance), all advisory |
| Memory | Instruction files; memory proposals | NSCLC.md in every system prompt; remember proposes, the clinician decides |
| Context management | Token accounting; compaction | Provider-neutral estimate; model-written summary of earlier turns near the window (deterministic fallback) |
| Checkpoints | Rewind | Snapshot before every turn of messages, case notes, plan and evidence ledger |
| Commands | Slash commands | 16 commands, one definition for CLI and web (/mdt, /plan, /review, /audit, /rewind …) |
| Extensibility | Model Context Protocol | Streamable-HTTP client (JSON or SSE replies, session ids); tools named mcp__server__tool |
| Interruption; headless use | Stop; stream-json | Ctrl-C or Stop ends the turn with a valid transcript; one JSON event per line |
| Authority | Settings outside the transcript | Role and configuration come from the caller; session files are re-validated and grant nothing |

*Concurrent natively (thread pool of up to six workers); serial in the browser (WebAssembly).
CLI, command-line interface; ReAct, reasoning and acting; SSE, server-sent events.
