# Workflow memory

Workflow memory lets the calling agent preserve a completed task's intent,
application, instructions and program, then retrieve it in a fresh session.
Learning and search run locally, without a model call or desktop input. The
calling agent writes the semantic notes and judges whether the outcome meets
the user's request. This is agent-assisted learning from runs; it does not
passively record the human's desktop or train model weights.

## Discover, execute, learn

1. Search by task and application: `ca memory search "house sun" --app KolourPaint`.
   Read a match with `ca memory show NAME`. Compare its starting state, procedure
   and evidence with the current task.
2. Observe the current application. Run a suitable remembered procedure with
   `ca memory run NAME --window APP`. Use `--args '{"parameter": "value"}'` for
   its typed inputs. If it does not fit, use the notes to write an adapted
   `ca execute` program. Keep target coordinates local to this fresh observation.
3. Inspect the result. Use meaningful program checks and visually verify artwork;
   a changed image or a successful dispatch alone does not establish correctness.
4. Preserve the completed run:

   ```sh
   ca memory learn house --run RUN_ID --task "Draw a house and sun" \
     --app KolourPaint --notes "Start with a blank canvas. Select each tool and colour explicitly. Describe canvas discovery, stroke order, inputs, verification and recovery here."
   ```

For a new or adapted workflow, pass `--remember NAME --task "TASK" --app "APP"`
and `--notes "PROCEDURE"` to `ca execute` to learn automatically after its checks
pass. A verified run updates an existing memory of that name; a failed or
unverified attempt leaves the previous procedure intact. Inspect the returned
`memory` or `memory_error` field. Automatic learning still requires the agent to
inspect the actual result before relying on the memory in future work.

Memories store a durable copy of the procedure and evidence summaries so rotating
run output does not remove the reusable knowledge. Referenced exported files and
images still need a permanent destination if they must remain available.
Use `ca memory list` to inspect the library and `ca memory forget NAME` to remove
an obsolete memory. `learn --update` revises an existing memory from a new run.
Non-successful runs can be learned as notes, but have no executable fast path.
Runs recorded before initial-window evidence was added are also notes-only.
Stored programs that call other fragments still need those fragments available;
prefer a standalone procedure when teaching a reusable flow.
Notes and stored programs are local data with the same user
permissions as other Computer Artist programs. Review imported memories before use.

## Guarded reuse

A remembered program is a fast path for a compatible starting state. Inspect the
live UI before using it. Its initial title, dimensions and available application
identity must match; moving a window does not invalidate window-local strokes.
These checks cannot establish canvas contents, selected tools or hidden dialogs;
the agent inspects those and the procedure initializes its controls explicitly.
The usual runtime ownership, geometry, deadline and
invocation-owned checks still apply. A mismatch is a reason to adapt the
procedure from fresh evidence, not to weaken its guards or use the host lane.
Use accessibility roles/names when available. Coordinate-based artwork can reuse
canvas-relative geometry while selecting its tools from current UI evidence.

Learning should preserve lessons from failures without presenting them as
successful executable procedures. Keep outcome evidence and uncertainty in notes;
program-defined checks remain claims rather than independent proof.

## Measure the benefit

Give two fresh agents the same task and initial app state. The first discovers,
executes, verifies and teaches the procedure. The second searches, inspects,
reuses and verifies it. Record wall time including agent reasoning/tool round
trips, and report program execution time separately. Exclude environment setup
from both. Include learning cost in the first total and report it separately
when possible. Keep both final screenshots and count retries/failures. A single
pair demonstrates that workflow's reuse benefit, not a general performance bound.
