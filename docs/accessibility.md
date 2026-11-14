# Reading and accessibility

Open **View → Reading and accessibility…** or press **Ctrl+Shift+A**. The same
options are available from **Settings → App → Reading options…**. Changes apply
immediately and are saved for the next launch.

- Choose **Dark** or **Light** to suit your lighting and reading preferences.
- Choose comfortable, large, larger, or extra large text, up to **200%**.
  **Ctrl++** (or **Ctrl+=**) increases text, **Ctrl+-** decreases it, and **Ctrl+0**
  restores the comfortable size. Windows display scaling also applies.
- Select **Reduce motion** to stop page fades and the pulsing scan badge.
  Animations start disabled for new preferences. Progress remains written out.
- Sound cues can be disabled under **View → Sound effects** or **Settings → App**.
  Sound is accompanied by visible status information.

Use **Tab / Shift+Tab** to move between controls, **Space** to press a button or
change a checkbox, and arrow keys to navigate results. **Enter** on a result
opens its details. **Ctrl+F** returns to results and selects the problem-search
text. **Ctrl+C** copies selected result rows. **Alt** accesses the menu bar.

Filters keep their visible labels while you type. Fields have associated labels
and accessible names. Summary cards use standard button roles. Priority and
command risk have written labels alongside their colours; colour is never the
only priority cue. Text readers and tables provide scrolling for long evidence.
At 200% text, the workspace scrolls and follows keyboard focus.

Automated checks cover representative rendered text contrast, selected text,
focus boundaries, keyboard behavior, saved preferences, table sizing, and
enlarged dialogs in both themes. They use the contrast calculation described by
[W3C](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), and native
labels and roles follow [Qt's accessibility support](https://doc.qt.io/qt-6/accessible-qwidget.html).
These checks do not establish complete accessibility conformance.

Before a release, manually check the packaged app with Windows Narrator or NVDA,
Windows contrast themes, Magnifier, and 100%, 150%, and 200% display scaling.
Confirm focus order, spoken field names and values, changing scan statuses,
table navigation, dialog scrolling, and return focus after closing dialogs.
Record the actual results in the release evidence; automated checks do not mark
these manual checks complete.
