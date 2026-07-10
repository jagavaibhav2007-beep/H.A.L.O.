# System Design: Computer Control (files, apps, lanes)

How Halo acts on the machine, and the three-lane model for *how* it acts.

## Capabilities
- **Files:** inspect, create, edit, move, organize — via native filesystem calls (Tier 1–2 per [permissions](04-permissions.md)).
- **Apps:** open/focus apps; drive them when needed.
- **GUI:** click/type into desktop apps that have no API.

## The three lanes (chosen per task by the Tool Executor)
| Lane | Mechanism | Cursor | You | When |
|---|---|---|---|---|
| **1 Fast** | filesystem calls, CLIs, APIs, MCP | none | free to work | default — whenever a programmatic path exists |
| **2 Takeover** | GUI on your real desktop (UI Automation → vision fallback) | your real cursor | wait/watch | GUI-only task needing your real logins |
| **3 Sandbox** | GUI in a VM with its own cursor | separate | free to work | GUI task you want isolated/observable; one-time login in the VM |

### Lane selection
```
task → has fast path? → Lane 1
     → else GUI-only → user pinned a lane? → use it
                     → else default Lane 2, announce it
```
Halo **always states the lane it used**; user can pin per task.

## GUI mechanism (Lanes 2/3)
- **Primary: Windows UI Automation** — drive by real UI elements (reliable).
- **Fallback: vision + coordinates** — screenshot → model picks target → click/type, for apps with no accessibility tree.

## Live view
- Lane 1: observed via the activity feed (no visuals).
- Lanes 2/3: UI shows a **live stream** of the desktop being driven; the lane indicator is always visible.

## Known tradeoff (from PRD §14.4)
Lane 3 gives concurrency but needs its own logins; Lane 2 has your logins but takes your screen. Same GUI action can't have both — chosen per task.

## Lane-3 VM choice (proposed — Windows 11 Home constraint)
This machine is **Windows 11 Home**, where **Hyper-V and Windows Sandbox are unavailable** (Pro/Enterprise only). WSL2 exists but is Linux — can't run the user's Windows apps. So:
- **If upgraded to Win 11 Pro (~$99 one-time): Windows Sandbox** — lightweight, disposable, auto-discards state, minimal setup. Re-login each session is inherent to isolation.
- **Staying on Home: VirtualBox** (free/OSS) with one persistent Windows guest — heavier (~4 GB RAM live, ~40 GB disk) but persistent logins remove the re-login pain.
- **MVP recommendation:** don't build Lane 3 at all initially. Ship Lanes 1–2; gate Lane 3 behind whichever VM is chosen when that phase is reached.

## Failure handling
- UI Automation element not found → retry once → fall back to vision → if still stuck, stop and ask (don't blind-click).
- Any destructive/irreversible GUI step still routes through the Tier-3 gate.
