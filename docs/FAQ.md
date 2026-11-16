# FAQ

Questions people ask about Mouse Double-Click Fixer. The [README](../README.md) says how the filter works, and [Troubleshooting](TROUBLESHOOTING.md) covers problems.

## Will it block my real double-clicks?

Not once Test and Tune has measured that button. Only a press within the filter window of the previous release is dropped, and the window Test and Tune recommends is no more than half of your fastest measured double-click (you can still pick a longer one in its last step). Until then, the default 46 ms window can catch a very fast double-click, one that leaves the button up for less than 46 ms. A window you set yourself under **Strength › Custom** has no such limit, and a longer one can drop real double-clicks.

## Does it delay my clicks?

Yes, the release of a click. Presses go straight through. The only press that waits is one that comes while a release is still on its way to apps: it goes out right after that release, usually within a millisecond or two, so apps see the two in order.

Every release of the left, right and middle buttons is held back for the filter window (46 ms until Test and Tune measures that button), so a contact dropout can't end a drag. If you move the mouse or click again, the release goes out as soon as an event from after the window arrives. On a still mouse it waits a little longer: the window plus an allowance for how late your computer delivers mouse events, which the app measures as it runs and keeps between 5 and 150 ms. On a quiet computer that is the window plus about 5 ms, for example 35 ms with a 30 ms window and 65 ms with a 60 ms window. On a busy computer the allowance grows with the delay, up to 150 ms. A click made in place is released sooner, as soon as you move the pointer off it. A shorter window means a quicker release, and so does turning the filter off for the buttons that don't bounce.

Scroll-wheel notches are never held back. Back and forward releases and clicks that pass untouched aren't held for the filter window, but like a press they can wait, usually a millisecond or two, behind a release the app is re-sending. On Windows, while the pointer is hidden or in a remote session, moving doesn't release a held click: it goes out with your next click or by the timer, where the pointer is then.

## Are drags protected from the start?

Yes, for the left, right and middle buttons. Every release is held, so a dropout is caught however soon after the press it comes, as long as the contact comes back within the filter window. A dropout longer than the window still ends the drag.

## Why does it need permission on macOS?

Blocking a click before other apps see it requires an event tap, and macOS only allows that for apps you have approved, under **Privacy & Security › Device Control and Data Access** (**Accessibility** on macOS 26 and earlier). The app asks only for mouse events, never keystrokes. Its pause shortcut needs no permission: see [Privacy](PRIVACY.md).

## Will it get me flagged in games?

The app doesn't add clicks of its own, but it does re-send some input: a release it held back for the filter window, and whatever comes while that release is on its way to apps (a press, another button's click, and on macOS pointer movement), so that apps see them in the order they happened. On Windows the app sends no pointer movement at all, only presses and releases: it puts the pointer where your hand has taken it, and after a drag let go while moving on the spot where the button came up and back, by placing it directly, which isn't input and which no input hook sees. Apps receive re-sent input as software input (on Windows, marked as injected), and the app's hook is in the input path whenever the filter is on. Some anti-cheat systems object to one or the other, and the app can't know what yours accepts.

If a game uses anti-cheat, turn the filter off, or quit the app, before you play it. Adding the game under **Exceptions › Apps** stops all filtering while it is in front and holds nothing new back, but the app's hook stays installed, so it doesn't make that safe.

**Pause during full-screen games** ([below](#can-it-stand-aside-while-i-play-a-game)) stops the filter while a game is in front, so nothing is held back or re-sent then. The app is still running, though, with its pause shortcut registered and a check every two seconds for a game, so it isn't a promise that a game's anti-cheat accepts it either.

## What are Automatic and Custom?

They are the two ways the app picks each button's filter window, under **Strength** (on **General** on macOS, on **Home** on Windows).

**Automatic** uses the window Test and Tune measured for that button, or one you applied from a History suggestion, and 46 ms for a button it hasn't measured. **Custom** shows a window for each filtered button, in milliseconds, for you to set (and one for the scroll wheel while its fix is on). Each keeps its own windows, so switching back finds them as you left them. If you updated from 1.0 and a button you filter has a window you set by hand, you start in Custom; otherwise in Automatic. Either way every window you had is kept.

## How do I pause it?

Choose **Pause for 15 Minutes** from the menu bar menu (macOS; also in the Dock menu), or **Pause for 15 minutes** from the notification area icon's right-click menu (Windows), or press the shortcut: ⌃⌥⌘P on macOS, Ctrl+Alt+Shift+P on Windows. While paused the menu says **Resume**, and so does the shortcut's next press. The status line says "Paused until 3:45 PM". When the time is up the filter starts again by itself.

Pause means something only while the filter is on. Turning the filter off ends a pause. A pause isn't saved: if you quit the app or restart the computer during one, the filter is back on when the app next starts (if it was on before). The shortcut can be turned off under **General › Pause shortcut** on macOS, **Settings › Pausing** on Windows. If another program already holds the same keys, the shortcut isn't available; **Copy Diagnostics** says so.

## Does the pause shortcut read my keyboard?

No. The app doesn't listen to the keyboard. It asks the system to tell it when one key combination is pressed, using `RegisterEventHotKey` on macOS and `RegisterHotKey` on Windows, and the system says nothing else about the keyboard. No keyboard event tap or hook is installed, and macOS needs no Input Monitoring permission for it. A test in the repository checks that nothing in the app uses a keyboard hook or tap. On macOS the key is the one where the P key is on a US keyboard, so on another layout the key in that place is the one that works; on Windows it is the P key of your layout.

## Can it stand aside while I play a game?

Yes, if you turn on **Exceptions › Games › Pause during full-screen games**. It is off by default. While it is on, the app asks every two seconds whether a full-screen game is in front. If one is, the filter stops, the status line says "Paused while … is in front", with the game's name where there is one, and it starts again when the game leaves the front. Choose **Resume** (or press the shortcut) to filter in that game anyway; the app then won't pause for that game again until it has left the front.

What counts as a game:

- **macOS:** the app in front says in its own bundle that it is a game (a Games category, or a request for Game Mode), and one of its windows covers a whole display. The app doesn't check whether macOS's Game Mode is actually on, as macOS gives no way to ask.
- **Windows:** a full-screen exclusive Direct3D app, or the window in front covers its whole monitor and either Windows says a full-screen app is running or the program is one on the list of games that Windows keeps for Game Bar. Browsers, video players, slide shows, meetings, remote desktop and virtual machine windows, and Windows' own screens never count.

Limits: a game on macOS whose bundle declares neither, or whose full-screen window belongs to another process (some launchers), isn't seen. On Windows, a game in a browser isn't seen, and another full-screen program that isn't a game may be taken for one. If it guesses wrong, choose **Resume**.

## It says another app may be changing clicks. What does that mean?

Some mouse utilities also change what the side buttons, the middle button or the wheel do, or filter clicks themselves. The app looks every minute, and when you bring it forward, at the names of the running programs for these: Logi Options+, Logitech Options, Logitech G HUB and Razer Synapse (macOS and Windows); Mac Mouse Fix, SteerMouse, LinearMouse and BetterMouse (macOS); X-Mouse Button Control and DoubleClickFix (Windows). If one is running, **General** (**Home** on Windows), the Test and Tune verdict and **History** say which, and what it may take over. "May" is the point: the note says what that utility can be set to do, not what it does on your computer. The app changes nothing in it. If side buttons or the wheel act up, check that utility's settings, or turn off its features for those.
