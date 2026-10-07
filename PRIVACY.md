# Privacy

design-critic runs entirely on your own machine. It has no server, no account and no telemetry.

## What it reads

When you ask Claude to review a website, the scripts open that website in a local browser (Playwright with Chromium) and read what the page shows: screenshots, text, colors, sizes and animations. If the page displays personal data, such as names or email addresses, that data appears in the screenshots and in the extracted text.

## What it stores

Everything is written to a `.design-critic/` folder inside the project you run it from:

- screenshots, the hover/focus sheet, motion frames and a short scroll video
- `capture.json` with the measurements and some of the page's text
- `findings.json` with the critique, and `report.html`

These files stay on your computer until you delete them. Nothing is kept anywhere else.

## What it sends

Nothing. The scripts only load the website you asked to review and the local files they create. They don't send data to the author or to any third-party service, and the HTML report doesn't load external resources.

Your conversation with Claude, including any screenshots Claude reads, is handled by Claude under Anthropic's own terms and privacy policy, as with any other file you share in a session.

## Contact

Questions or concerns: open an issue at https://github.com/Fepe7/design-critic/issues.
