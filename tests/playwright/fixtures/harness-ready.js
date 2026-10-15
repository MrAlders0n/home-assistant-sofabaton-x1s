// Opens a card harness page and waits for its module to have run.
//
// The harness is a module script that sets its window global as it runs;
// page.goto() resolving on the load event does not prove that. CI (Windows
// runner, 2026-10-15) handed back pages where the global never appeared while
// every other test passed, so a page whose harness is missing is reloaded
// once. A harness that is genuinely broken still fails after the reload.

export async function openHarness(page, url, global = "__remoteCardHarness") {
  const ready = () => page.waitForFunction((name) => Boolean(window[name]), global, { timeout: 5_000 });
  await page.goto(url);
  try {
    await ready();
  } catch {
    await page.reload();
    await ready();
  }
}
