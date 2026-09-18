// Only run on the private test compositor's session bus.
for (const window of workspace.windowList()) {
    if (window.caption === 'Your typing space') {
        window.frameGeometry = {x: 30, y: 40, width: 510, height: 770};
    } else if (window.caption === 'Agent canvas') {
        window.frameGeometry = {x: 560, y: 40, width: 790, height: 770};
    }
}
