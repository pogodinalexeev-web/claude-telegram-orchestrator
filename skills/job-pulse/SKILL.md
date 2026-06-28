---
name: job-pulse
description: Pulse of the main career track — what's #next, where it's stuck, which decisions accumulated without action. Use for "/job-pulse", "how's the job search going", "status on the track".
---

# /job-pulse — main track status

## Algorithm
1. **Read**:
   - Main track file — details of the track.
   - Month roadmap — monthly plan.
   - Track chronicle — decision history.
   - Grep across career projects for `#next`, `#waiting`, `#blocker`.
2. **Read the main project manual yourself** (last 200 lines via `tail -200`, without delegating). Extract: last 3 actions, last contact with key person, open promises.
3. **Formulate the pulse** in format:
   ```
   📊 Pulse — Career track
   
   Currently #next:
   - <list of open tasks>
   
   #waiting:
   - <what we're waiting for from external people>
   
   Last contact with key contact: <date>, <short essence>
   
   Days without movement: <N>
   
   Stall signals:
   - <if #waiting >7 days — flag>
   - <if no #next — critical flag>
   - <if last Decision >7 days ago — possibly need a new one>
   
   Suggestion:
   - <one concrete action for today/tomorrow>
   ```

## Rules
- Impartially. If the track has stalled — say so directly.
- If #next is empty — this is a red flag requiring action NOW.
- Suggested action — one concrete, not a list.
