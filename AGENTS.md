# Computer Artist

This project was reset on 18 September 2026. Read FEATURES.md for the intended
capabilities and build sequence. No prior implementation remains.

- The goal is independent agent input in applications on the user's actual
  desktop while the user continues using their own pointer and keyboard.
- Prove KWin input separation and existing-app compatibility first. A private
  Xvfb workspace or separate desktop does not meet the product goal. A nested
  compositor may be used as a development test harness.
- Treat app-assigned seats and client-specific seat exposure as hypotheses
  until tested. Report restrictions for native Wayland and XWayland separately.
- Keep compositor experiments separate from the running host compositor.
- Preserve the broader capabilities in FEATURES.md: conditional programs, continuous
  observation, precise gestures, handoff, verified outcomes, and reusable work.
- Distinguish proposed, implemented, and verified behavior. Do not inherit old
  test results or describe input isolation as a security sandbox.
- Never silently fall back to moving the human's pointer or stealing focus.
- Reason through the user's intent before implementing. Check that the proposed
  structure makes sense, and choose the simplest design that fulfills it. Do not
  carry forward unnecessary abstractions just because the old implementation
  used them. For example, `window/layout` and `window/api-fragmants` are siblings
  under one `window` folder; do not invent `window/api-fragmants/windows/ID`.
- Resolve implementation choices through reasoning and evidence. When a choice
  genuinely depends on the user's preference and cannot be inferred from their
  instructions, ask a concise question before implementing that choice.
