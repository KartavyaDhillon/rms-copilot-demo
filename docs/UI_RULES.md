# UI rules (apply to every new screen)

1. **Every button that starts work shows a spinning circle** until the work finishes, so nobody wonders if the click did anything.
   `platform/js/rms-busy.js` does this automatically for any button whose click triggers a `fetch` (loaded on the platform pages and
   the Process Center). For work that continues after the request returns (long runs, polling), put your own `<span class="rms-spin"></span>`
   in the button for the whole duration (buttons that already contain a "spin" element are left alone by the automatic one).
2. Button names must be readable by a non-technical person.
3. Always show who started a run and a clear result message at the end (also "already there / nothing to do" cases).
