# Mobile keyboard: input jumping above the visible area

Reported on iPhone. Live-device verification inside Telegram is still required after deployment.

## Confirmed code problems

- Focusing a textarea used to hide sibling blocks and reset window scroll to zero.
  This moved the focused element while the browser was also revealing it.
- Switching from a textarea to an input could retain the hidden sibling state.
- Oversized textareas were bottom-aligned and could end up above the visible area.
- Dialog inputs attempted to scroll the locked document rather than the dialog.
- The fallback without VisualViewport never detected a keyboard from innerHeight alone.
- Header/safe-area bounds were replaced with a fixed 60px assumption.

## Changes

Keep document layout stable on focus, without compose mode or scroll restoration.
Measure the visible area through VisualViewport and Telegram's reported height;
respect safe areas and the actual keyboard toolbar. Scroll the nearest scrollable
container before the document. Align oversized textarea tops instead of their bottoms.
Clear keyboard state after focus loss and detached inputs. Size sheets to the available
height and reserve toolbar space in Telegram as well as browser mode.

## Verification

- Node regression suite runs the actual keyboard controller with controlled viewport,
  focus and scroll events. Covers 12 scenarios, including iOS offset, large textareas,
  field switching, nested dialogs, missing VisualViewport and detached inputs.
- The first nine scenarios failed against the previous controller and passed after the fix.
- Complete Python suite: 1088 passed, 1 skipped before the final detached-focus guard;
  targeted frontend/API tests were rerun after that guard.
- Browser mock UI at 390×844 reduced to 390×460 while entering a meal: text persisted,
  field remained visible, no hidden sibling blocks. Search input in a sheet remained visible.
- Screenshot: `docs/keyboard-input-mobile.jpg` (browser check, not an actual iPhone screenshot).
- Real iPhone + Telegram software keyboard still requires a live-device check after deployment.

## References

- [Telegram Mini App viewport fields](https://core.telegram.org/bots/webapps)
- [MDN VisualViewport](https://developer.mozilla.org/en-US/docs/Web/API/VisualViewport)

These changes do not modify the database, nutrition calculations or stored diary data.
