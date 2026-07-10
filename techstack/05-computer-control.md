# Tech Stack: Computer Control (files, apps, lanes)

Design: [systemdesign/05-computer-control](../systemdesign/05-computer-control.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| File ops | Python stdlib (`pathlib`, `shutil`) | Lane 1, native |
| Launch/focus apps | Windows APIs (`subprocess`, `pywin32`) | Lane 1 |
| GUI (primary) | **Windows UI Automation** via `uiautomation`/`pywinauto` | element-based |
| GUI (fallback) | screenshot → model → `pyautogui` click/type | vision-based |
| Screen capture | `mss` / native | for vision + live stream |
| Sandbox (Lane 3) | **Windows Sandbox** (needs Win 11 Pro) or **VirtualBox** (free, works on Home) | own cursor; app logins inside; deferred out of MVP |

## Cost note
- **Lane 1 & GUI mechanics = free/local.**
- The **vision fallback** costs a heavy-model (vision) call per screenshot decision — used sparingly, only when UI Automation can't find the element.
- Sandbox VM = local compute (RAM/CPU), no cloud.

## Lane-3 note
- This machine is **Win 11 Home** — no Hyper-V / Windows Sandbox. Proposed: Windows Sandbox if upgraded to Pro, else VirtualBox. Lane 3 is deferred out of MVP; decide when that phase is reached. See [systemdesign/05-computer-control](../systemdesign/05-computer-control.md).
