import { expect, test } from "@playwright/test";

test.describe("tools-card browser harness", () => {
  test("glass theme: dropdown lists inside a blurred card and dialog open on their trigger", async ({ page }) => {
    // Liquid Glass puts backdrop-filter on ha-card and (via
    // --ha-dialog-surface-backdrop-filter) on the dialog surface. A
    // backdrop-filter makes the element the containing block of its
    // position: fixed descendants, so the fixed dropdown lists measured from
    // the wrong corner and the dialog's overflow clipped them. The helpers
    // now measure against that containing block, and the dialog's blur lives
    // on a pseudo-element so the dialog itself never becomes one.
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.goto("/tests/tools-card-harness.html?scenario=macro-add-step&theme=Liquid%20Glass%7Cdark&width=900");
    const detail = page.locator("sofabaton-control-panel sofabaton-backup-tab sofabaton-edit-detail-view");
    await expect(detail.locator(".dialog")).toBeVisible();
    await detail.locator(".dialog button", { hasText: "Cancel" }).click();
    await detail.locator(".back-btn").click();

    const rect = (locator) => locator.evaluate((el) => {
      const b = el.getBoundingClientRect();
      return { left: Math.round(b.left), right: Math.round(b.right), top: Math.round(b.top), bottom: Math.round(b.bottom) };
    });
    const card = page.locator("sofabaton-control-panel ha-card");
    expect(await card.evaluate((el) => getComputedStyle(el).backdropFilter)).toBe("blur(8px)");

    // The role menu in the activity editor: under the blurred card it opens
    // flush with its trigger (here above it: the card's bottom is close).
    const roleTrigger = detail.locator(".role-trigger").first();
    await roleTrigger.click();
    const roleMenu = detail.locator(".role-menu");
    await expect(roleMenu).toBeVisible();
    const [trig, menu, cardBox] = [await rect(roleTrigger), await rect(roleMenu), await rect(card)];
    expect(menu.right).toBe(trig.right);
    expect(menu.bottom === trig.top - 4 || menu.top === trig.bottom + 4).toBe(true);
    expect(menu.top).toBeGreaterThanOrEqual(cardBox.top);
    await detail.locator(".member-add-backdrop").click();

    // The macro picker inside the button-assignment dialog.
    await detail.locator(".edit-selection-row--footer").click();
    await detail.locator(".quick-access-add-btn").click();
    const dialog = detail.locator(".dialog");
    await expect(dialog).toBeVisible();
    expect(await dialog.evaluate((el) => getComputedStyle(el).backdropFilter)).toBe("none");
    expect(await dialog.evaluate((el) => getComputedStyle(el, "::before").backdropFilter)).toBe("blur(8px)");
    await detail.locator(".kind-seg-btn", { hasText: "Macro" }).first().click();
    const pickerTrigger = detail.locator(".macro-picker-trigger").first();
    await pickerTrigger.click();
    const picker = detail.locator(".macro-picker-menu");
    await expect(picker).toBeVisible();
    await expect(picker.locator(".macro-picker-option", { hasText: "Volume Combo" })).toBeVisible();
    const [pt, pm] = [await rect(pickerTrigger), await rect(picker)];
    expect(pm.left).toBe(pt.left);
    expect(pm.right).toBe(pt.right);
    expect(pm.top).toBe(pt.bottom + 4);
    // The dialog's title is still there: nothing of the dialog body went blank.
    await expect(dialog.locator(".dialog-title")).toContainText("Add button assignment");
  });

  test("drives every supported locale through the visible harness control", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    const expected = [
      ["en", ["Hub", "Automation", "Backup"]],
      ["en-gb", ["Hub", "Automation", "Backup"]],
      ["de", ["Hub", "Automatisierung", "Backup"]],
      ["es", ["Hub", "Automatización", "Backup"]],
      ["fr", ["Hub", "Automatisation", "Sauvegarde"]],
      ["nl", ["Hub", "Automatisering", "Back-up"]],
      ["zh-hans", ["Hub", "自动化", "备份"]],
    ];

    expect(await page.evaluate(
      () => window.__toolsCardHarness.supportedLanguages,
    )).toEqual(expected.map(([language]) => language));

    for (const [language, labels] of expected) {
      await page.getByLabel("Language", { exact: true }).selectOption(language);
      await page.waitForFunction((wanted) => {
        const root = document.querySelector("sofabaton-control-panel")?.shadowRoot;
        const actual = [...(root?.querySelectorAll(".tab-btn-label") ?? [])]
          .map((label) => label.textContent?.trim());
        return JSON.stringify(actual) === JSON.stringify(wanted);
      }, labels);
    }

    // The card-picker entry follows the active language (CR-X7-2).
    const pickerName = () => page.evaluate(
      () => window.customCards.find((card) => card.type === "sofabaton-control-panel")?.name,
    );
    expect(await pickerName()).toBe("Sofabaton 控制面板");
    await page.getByLabel("Language", { exact: true }).selectOption("en");
    await expect.poll(pickerName).toBe("Sofabaton Control Panel");

    expect(await page.evaluate(
      () => window.__toolsCardHarness.getUnhandledCalls(),
    )).toEqual([]);
  });

  test("icon picker tooltips and accessible labels follow the language while brands stay unchanged", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    for (const [language, music] of [["en", "Music"], ["en-gb", "Music"], ["de", "Musik"], ["es", "Música"], ["fr", "Musique"], ["nl", "Muziek"], ["zh-hans", "音乐"]]) {
      await page.evaluate(async (language) => {
        await window.__toolsCardHarness.setLanguage(language);
        await window.__toolsCardHarness.loadScenario("add-device");
      }, language);
      await page.locator("sofabaton-control-panel .cache-dialog-appearance-toggle").click();
      const picker = page.locator("sofabaton-control-panel .cache-dialog-appearance-pane");
      await expect(picker.getByRole("button", { name: music, exact: true })).toHaveAttribute("title", music);
      await expect(picker.getByRole("button", { name: "Netflix", exact: true })).toHaveAttribute("title", "Netflix");
    }
  });

  test("the admins-only option shows other users a notice (CR-X2-1)", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("1"));
    const setUser = (isAdmin) => page.evaluate((admin) => {
      const card = window.__toolsCardHarness.getCard();
      const base = window._harnessHass;
      card.setConfig({ admin_only: true });
      card.hass = Object.assign(Object.create(Object.getPrototypeOf(base)), base, { user: { is_admin: admin } });
    }, isAdmin);
    const view = () => page.evaluate(() => {
      const root = window.__toolsCardHarness.getCard().shadowRoot;
      return {
        notice: root.querySelector(".backend-unavailable-title")?.textContent?.trim() ?? null,
        tabs: root.querySelectorAll(".tab-btn-label").length,
      };
    });

    await setUser(false);
    await expect.poll(view).toEqual({ notice: "Admins only", tabs: 0 });
    await setUser(true);
    await expect.poll(view).toEqual({ notice: null, tabs: 3 });

    const emitted = await page.evaluate(async () => {
      const editor = document.createElement("sofabaton-control-panel-editor");
      editor.setConfig({ card_height: 500 });
      document.body.appendChild(editor);
      const configs = [];
      editor.addEventListener("config-changed", (event) => configs.push(event.detail.config));
      const box = editor.querySelector("#tools-card-admin-only");
      box.click();
      editor.setConfig(configs[0]);
      editor.querySelector("#tools-card-admin-only").click();
      editor.remove();
      return configs;
    });
    expect(emitted).toEqual([{ card_height: 500, admin_only: true }, { card_height: 500 }]);
  });

  test("compact controls do not clip at narrow width in any supported locale", async ({ page }) => {
    test.setTimeout(60_000);
    await page.goto("/tests/tools-card-harness.html?width=360");

    const languages = await page.evaluate(
      () => window.__toolsCardHarness.supportedLanguages,
    );
    const scenarios = ["1", "add-device", "activities-capture", "backup-edit", "automation-events", "18", "21", "24",
      "wifi-delivery-failed", "wifi-create-mqtt-no-broker", "settings-x2-broker"];
    const failures = [];

    for (const language of languages) {
      await page.evaluate(
        (value) => window.__toolsCardHarness.setLanguage(value),
        language,
      );
      for (const scenario of scenarios) {
        const clipped = await page.evaluate(async (scenarioId) => {
          await window.__toolsCardHarness.loadScenario(scenarioId);
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

          if (scenarioId === "settings-x2-broker") {
            const card = window.__toolsCardHarness.getCard();
            // Wait for the broker status before opening its form.
            await card._brokerForm.load("hub-1");
            await card.updateComplete;
            card.shadowRoot.querySelector(".setting-broker").click();
            await card.updateComplete;
          }
          if (scenarioId === "wifi-delivery-failed") {
            // The delivery dropdown, open: its title, status and method choice.
            const tab = window.__toolsCardHarness.getCard().shadowRoot.querySelector("sofabaton-wifi-commands-tab");
            tab.shadowRoot.querySelector(".delivery-pill").click();
            await tab.updateComplete;
          }

          const card = window.__toolsCardHarness.getCard();
          const roots = [];
          const visit = (root) => {
            roots.push(root);
            for (const element of root.querySelectorAll("*")) {
              if (element.shadowRoot) visit(element.shadowRoot);
            }
          };
          if (card?.shadowRoot) visit(card.shadowRoot);

          const failures = [];
          for (const root of roots) {
            for (const element of root.querySelectorAll("button, select, [role='tab'], .chip, .pill, .delivery-pop, .delivery-head, .delivery-choice, .broker-dialog, .broker-fields, .broker-login-option, .broker-dialog .dialog-footer-actions")) {
              const text = element.textContent?.replace(/\s+/g, " ").trim();
              if (!text || !element.getClientRects().length) continue;
              if (element.scrollWidth > element.clientWidth + 1) {
                failures.push({
                  text,
                  clientWidth: element.clientWidth,
                  scrollWidth: element.scrollWidth,
                });
              }
            }
          }
          return failures;
        }, scenario);

        if (clipped.length) failures.push({ language, scenario, clipped });
      }
    }

    expect(failures).toEqual([]);
  });

  test("switches locale, theme, and width while keeping a shareable URL", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html?width=320");

    await expect(page.getByLabel("Card width", { exact: true })).toHaveValue("360");

    await page.getByLabel("Language", { exact: true }).selectOption("nl");
    await page.waitForFunction(() => {
      const root = document.querySelector("sofabaton-control-panel")?.shadowRoot;
      return [...(root?.querySelectorAll(".tab-btn-label") ?? [])]
        .some((label) => label.textContent?.trim() === "Automatisering");
    });

    await page.getByLabel("Theme", { exact: true }).selectOption("dark");
    await page.getByLabel("Card width", { exact: true }).selectOption("360");

    await expect(page.locator("body")).toHaveAttribute("data-theme", "dark");
    await expect(page.locator("#active-language")).toHaveText("Nederlands");
    await expect(page.locator("#active-theme")).toHaveText("Dark");
    expect(await page.evaluate(
      () => document.documentElement.style.getPropertyValue("--harness-card-width"),
    )).toBe("360px");
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    )).toBe(true);

    const params = new URL(page.url()).searchParams;
    expect(params.get("scenario")).toBe("1");
    expect(params.get("lang")).toBe("nl");
    expect(params.get("theme")).toBe("dark");
    expect(params.get("width")).toBe("360");
  });

  test("applies Home Assistant base palette and fixture community themes the way HA does", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html?scenario=automation-events&theme=Caule%20Black%20Purple");

    // Fixture loaded: HA's real defaults replace the hand-picked palette and
    // the switcher lists the community themes (mode-aware ones twice).
    expect(await page.evaluate(() => window.__toolsCardHarness.themeFixtureLoaded)).toBe(true);
    const options = await page.evaluate(() => window.__toolsCardHarness.themeOptions);
    expect(options.map((option) => option.value)).toEqual(
      expect.arrayContaining(["light", "dark", "Caule Black Purple", "Material You|light", "Material You|dark"]),
    );
    expect(await page.evaluate(() => document.getElementById("harness-fallback-palette").disabled)).toBe(true);

    const resolve = (cssValue) => page.evaluate((value) => {
      const probe = document.createElement("span");
      probe.style.color = value;
      document.body.appendChild(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    }, cssValue);

    // Flat theme from the URL: HA keeps darkMode=false for themes without
    // modes, the appearance is still detected as dark, and the theme's own
    // var() chains resolve over HA's base (secondary text -> --disabled-color).
    await expect(page.locator("#active-theme")).toHaveText("Caule Black Purple");
    expect(await page.evaluate(() => window.__toolsCardHarness.getThemeState()))
      .toMatchObject({ name: "Caule Black Purple", haDarkMode: false, appearance: "dark" });
    await expect(page.locator("body")).toHaveAttribute("data-theme", "dark");
    expect(await resolve("var(--secondary-text-color)")).toBe("rgb(57, 57, 57)");
    expect(await resolve("var(--ha-card-background)")).toBe("rgb(25, 25, 25)");

    // Mode-aware theme: the dark block layers over HA's dark base vars and
    // hex values get an --rgb-* companion like processTheme generates.
    await page.getByLabel("Theme", { exact: true }).selectOption("Material You|dark");
    await expect(page.locator("#active-theme")).toHaveText("Material You · dark");
    expect(await page.evaluate(() => window.__toolsCardHarness.getThemeState()))
      .toMatchObject({ name: "Material You", haDarkMode: true, appearance: "dark" });
    expect(await page.evaluate(
      () => document.documentElement.style.getPropertyValue("--rgb-card-background-color"),
    )).toBe("");
    expect(await page.evaluate(
      () => window._harnessHass.themes,
    )).toMatchObject({ darkMode: true, theme: "Material You" });
    expect(new URL(page.url()).searchParams.get("theme")).toBe("Material You|dark");

    // HA default light: HA 2026 neutral ramp, and the inline theme vars are
    // cleared again.
    await page.getByLabel("Theme", { exact: true }).selectOption("light");
    await expect(page.locator("#active-theme")).toHaveText("Light");
    expect(await resolve("var(--primary-text-color)")).toBe("rgb(20, 20, 20)");
    expect(await resolve("var(--secondary-text-color)")).toBe("rgb(94, 94, 94)");
    expect(await page.evaluate(() => document.documentElement.style.length)).toBe(
      await page.evaluate(() => [...document.documentElement.style].filter((name) => name === "--harness-card-width").length),
    );

    // Wallpaper theme: the view area paints --lovelace-background (the
    // theme's image) and the card gets the theme's backdrop blur.
    await page.getByLabel("Theme", { exact: true }).selectOption("visionos|dark");
    expect(await page.evaluate(() => getComputedStyle(document.querySelector(".card-area")).backgroundImage))
      .toContain("night.jpg");
    expect(await page.evaluate(() => {
      const card = document.querySelector("sofabaton-control-panel");
      const haCard = card.shadowRoot.querySelector("ha-card");
      return getComputedStyle(haCard).backdropFilter;
    })).toMatch(/blur/);

    // HA default dark: the dark base vars only.
    await page.getByLabel("Theme", { exact: true }).selectOption("dark");
    expect(await resolve("var(--primary-text-color)")).toBe("rgb(225, 225, 225)");
    expect(await resolve("var(--card-background-color)")).toBe("rgb(28, 28, 28)");
    await expect(page.locator("body")).toHaveAttribute("data-theme", "dark");
  });

  test("restores a localized Automation Events scenario from URL state", async ({ page }) => {
    await page.goto(
      "/tests/tools-card-harness.html"
      + "?scenario=automation-events&lang=de&theme=light&width=900",
    );

    await expect(page.locator("#active-scenario-name"))
      .toHaveText("Events — configured actions");
    await expect(page.getByLabel("Language", { exact: true })).toHaveValue("de");
    expect(await page.evaluate(
      () => document.documentElement.style.getPropertyValue("--harness-card-width"),
    )).toBe("900px");
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    )).toBe(true);

    await page.waitForFunction(() => {
      const card = document.querySelector("sofabaton-control-panel");
      const tab = card?.shadowRoot?.querySelector("sofabaton-wifi-commands-tab");
      return tab?.shadowRoot?.textContent?.includes("Hub-Ereignisse");
    });

    const result = await page.evaluate(() => ({
      calls: window.__toolsCardHarness.getCalls()
        .filter((call) => call.channel === "ws")
        .map((call) => call.message?.type),
      unhandled: window.__toolsCardHarness.getUnhandledCalls(),
    }));
    expect(result.calls).toContain("sofabaton_x1s/hub_event_actions/get");
    expect(result.calls).toContain("sofabaton_x1s/wifi_event/list");
    expect(result.unhandled).toEqual([]);
  });

  test("fails unknown websocket operations visibly", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(async () => {
      try {
        await window.__toolsCardHarness.getCard()._snapshot.hass.callWS({
          type: "sofabaton_x1s/not_a_real_operation",
        });
      } catch (_error) {
        // Expected: the harness is deliberately strict.
      }
    });

    await expect(page.locator("#unhandled-api-count")).toHaveText("1 unhandled API call");
    const unhandled = await page.evaluate(
      () => window.__toolsCardHarness.getUnhandledCalls(),
    );
    expect(unhandled).toEqual([expect.objectContaining({
      channel: "ws",
      message: { type: "sofabaton_x1s/not_a_real_operation" },
    })]);
  });

  test("loads every scenario in English and Chinese without setup or API failures", async ({ page }) => {
    test.setTimeout(60_000);
    await page.goto("/tests/tools-card-harness.html");
    const scenarioIds = await page.evaluate(
      () => window.__toolsCardHarness.scenarioIds,
    );
    const failures = [];

    for (const language of ["en", "zh-hans"]) {
      await page.evaluate(
        (value) => window.__toolsCardHarness.setLanguage(value),
        language,
      );
      for (const scenarioId of scenarioIds) {
        const result = await page.evaluate(async (id) => {
          let error = null;
          try {
            await window.__toolsCardHarness.loadScenario(id);
          } catch (reason) {
            error = String(reason?.message ?? reason);
          }
          return {
            error,
            unhandled: window.__toolsCardHarness.getUnhandledCalls(),
          };
        }, scenarioId);
        if (result.error || result.unhandled.length) {
          failures.push({ language, scenarioId, ...result });
        }
      }
    }

    expect(failures).toEqual([]);
  });

  test("interactive scenarios reach the visible state promised by their labels", async ({ page }) => {
    test.setTimeout(45_000);
    await page.goto("/tests/tools-card-harness.html?lang=zh-hans");

    const inspect = async (scenarioId) => page.evaluate(async (id) => {
      await window.__toolsCardHarness.loadScenario(id);
      const card = window.__toolsCardHarness.getCard();
      const root = card?.shadowRoot;
      const editor = root?.querySelector("sofabaton-activities-tab");
      const wifi = root?.querySelector("sofabaton-wifi-commands-tab");
      const backup = root?.querySelector("sofabaton-backup-tab");
      return {
        selectedTab: card?._snapshot?.selectedTab ?? null,
        selectedCacheSection: card?._snapshot?.selectedCacheSection ?? null,
        openEntity: card?._snapshot?.openEntity ?? null,
        contentsGeneration: card?._snapshot?.contents?.hubs?.[0]?.cache_generation ?? null,
        firstActivity: card?._snapshot?.contents?.hubs?.[0]?.activities?.[0]?.name ?? null,
        addDeviceOpen: Boolean(card?._addDeviceOpen),
        refreshSpinner: Boolean(root?.querySelector(".icon-btn.spinning")),
        editor: editor ? { kind: editor.kind, stage: editor._stage } : null,
        wifi: wifi ? {
          section: wifi.selectedSection,
          deviceKey: wifi._selectedDeviceKey,
          commandModal: wifi._activeCommandModal,
          createModalOpen: wifi._createDeviceModalOpen,
          deleteDeviceKey: wifi._deleteDeviceKey,
        } : null,
        backup: backup ? {
          section: backup.selectedSection,
          backupStatus: backup._backupProgress?.status ?? null,
          restoreStatus: backup._restoreProgress?.status ?? null,
          hasRestoreBundle: Boolean(backup._restoreBundle),
          editDetailKind: backup._editDetailKind ?? null,
        } : null,
      };
    }, scenarioId);

    expect(await inspect("8")).toMatchObject({
      selectedTab: "cache",
      selectedCacheSection: "devices",
    });
    expect(await inspect("9")).toMatchObject({
      selectedCacheSection: "devices",
      openEntity: "dev-7",
    });
    expect(await inspect("add-device")).toMatchObject({
      selectedCacheSection: "devices",
      addDeviceOpen: true,
    });
    expect(await inspect("10")).toMatchObject({
      contentsGeneration: 2,
      firstActivity: "Watch TV (renamed elsewhere)",
    });
    expect(await inspect("11")).toMatchObject({
      selectedCacheSection: "devices",
      refreshSpinner: true,
    });
    expect(await inspect("activities-capture")).toMatchObject({
      editor: { kind: "activity" },
    });
    expect(await inspect("device-editor")).toMatchObject({
      editor: { kind: "device" },
    });
    expect(await inspect("backup-progress")).toMatchObject({
      backup: { section: "make", backupStatus: "running" },
    });
    expect(await inspect("backup-edit")).toMatchObject({
      backup: { section: "edit", editDetailKind: "activity" },
    });
    expect(await inspect("restore-selection")).toMatchObject({
      backup: { section: "restore", hasRestoreBundle: true },
    });
    expect(await inspect("restore-complete")).toMatchObject({
      backup: { section: "restore", restoreStatus: "success" },
    });
    expect(await inspect("automation-events")).toMatchObject({
      wifi: { section: "hub_events" },
    });
    expect(await inspect("18")).toMatchObject({
      wifi: { section: "wifi", deviceKey: null },
    });
    expect(await inspect("21")).toMatchObject({
      wifi: { deviceKey: "dev-lr" },
    });
    expect(await inspect("22")).toMatchObject({
      wifi: { deviceKey: "dev-lr", commandModal: "details" },
    });
    expect(await inspect("24")).toMatchObject({
      wifi: { deviceKey: null, createModalOpen: true },
    });
    expect(await inspect("25")).toMatchObject({
      wifi: { deviceKey: null, deleteDeviceKey: "dev-br" },
    });
  });

  test("creates a device from the Hub tab dialog and opens the live editor on it", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("add-device"));

    const card = page.locator("sofabaton-control-panel");
    const dialog = card.locator(".cache-dialog");
    await expect(dialog).toBeVisible();
    // Class list follows the hub line (X1S in the connected fixture): generic
    // Wifi HTTP present, MQTT absent, Bluetooth never offered.
    const classes = await dialog.locator(".cache-dialog-select option").evaluateAll(
      (options) => options.map((option) => option.value),
    );
    expect(classes).toEqual(["ir", "wifi_roku", "wifi_hue", "wifi_sonos", "wifi_ip"]);

    await dialog.locator(".cache-dialog-input[type=text]").fill("Hue Bridge");
    await dialog.locator(".cache-dialog-select").selectOption("wifi_hue");
    // The Home Assistant hint only shows for the classes it applies to.
    await expect(dialog.locator(".cache-dialog-hint")).toHaveCount(0);
    await dialog.locator(".cache-dialog-select").selectOption("wifi_ip");
    await expect(dialog.locator(".cache-dialog-hint")).toHaveCount(1);
    await dialog.locator(".cache-dialog-select").selectOption("wifi_hue");
    await dialog.locator(".cache-footer-btn--primary").click();

    await page.waitForFunction(() => {
      const card = window.__toolsCardHarness.getCard();
      return card?._editingEntity?.kind === "device";
    });
    const result = await page.evaluate(() => {
      const card = window.__toolsCardHarness.getCard();
      const create = window.__toolsCardHarness.getCalls()
        .filter((call) => call.channel === "ws" && call.message?.type === "sofabaton_x1s/device/create")
        .map((call) => call.message);
      return {
        create,
        editing: card._editingEntity,
        dialogOpen: card._addDeviceOpen,
        unhandled: window.__toolsCardHarness.getUnhandledCalls(),
      };
    });
    expect(result.create).toEqual([
      { type: "sofabaton_x1s/device/create", entry_id: "hub-1", name: "Hue Bridge", device_class: "wifi_hue" },
    ]);
    expect(result.editing).toEqual({ kind: "device", id: 40 });
    expect(result.dialogOpen).toBe(false);
    expect(result.unhandled).toEqual([]);
  });

  test("the latest scenario wins when setup overlaps", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(async () => {
      await Promise.allSettled([
        window.__toolsCardHarness.loadScenario("21"),
        window.__toolsCardHarness.loadScenario("24"),
      ]);
    });

    await expect(page.locator("#active-scenario-name")).toHaveText("24. Create device modal");
    expect(new URL(page.url()).searchParams.get("scenario")).toBe("24");
    expect(await page.evaluate(() => {
      const card = window.__toolsCardHarness.getCard();
      const wifi = card?.shadowRoot?.querySelector("sofabaton-wifi-commands-tab");
      return {
        selectedDeviceKey: wifi?._selectedDeviceKey ?? null,
        createModalOpen: wifi?._createDeviceModalOpen ?? false,
      };
    })).toEqual({ selectedDeviceKey: null, createModalOpen: true });
  });

  test("a refused Wifi sync names its reason in the dock, never as backend text", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("wifi-sync-refused"));
    await page.waitForFunction(() => {
      const tab = window.__toolsCardHarness.getCard().shadowRoot?.querySelector("sofabaton-wifi-commands-tab");
      return Boolean(tab?.shadowRoot?.querySelector(".detail-sync-btn:not([disabled])"));
    });
    await page.evaluate(() => {
      const tab = window.__toolsCardHarness.getCard().shadowRoot.querySelector("sofabaton-wifi-commands-tab");
      tab.shadowRoot.querySelector(".detail-sync-btn").click();
    });
    const dock = () => page.evaluate(() =>
      window.__toolsCardHarness.getCard().shadowRoot?.querySelector(".card-bottom-dock-status")?.textContent?.trim() ?? "");
    await expect.poll(dock).toBe("Activities on the hub changed. Re-select this Wifi Device's activities, save, and sync again.");
    const calls = await page.evaluate(() => window.__toolsCardHarness.getCalls());
    expect(calls.some((call) => call.channel === "service")).toBe(false);
    const allText = await page.evaluate(() => {
      const collect = (root) => [...root.querySelectorAll("*")].map((el) =>
        (el.shadowRoot ? collect(el.shadowRoot) : "") + (el.childElementCount ? "" : el.textContent)).join(" ");
      return collect(window.__toolsCardHarness.getCard().shadowRoot);
    });
    expect(allText).not.toContain("Failed Activity validation");
  });

  // How Wifi Command presses reach Home Assistant
  // (docs/internal/wifi-webhook-transport-plan.md, wave 3).

  // The banner lives in the HTTP / MQTT pill's dropdown now.
  const wifiPill = (page) => page.locator("sofabaton-wifi-commands-tab .detail-title-row .delivery-pill");
  const wifiPop = (page) => page.locator("sofabaton-wifi-commands-tab .delivery-pop");
  const editorPill = (page) => page.locator("sofabaton-activities-tab sofabaton-edit-detail-view .delivery-pill");
  const editorPop = (page) => page.locator("sofabaton-activities-tab sofabaton-edit-detail-view .delivery-pop");
  const wsTypes = (page) => page.evaluate(() => window.__toolsCardHarness.getCalls().map((call) => call.message?.type));

  test("a failed delivery test turns the pill amber; its dropdown names the flow to allow, and Test clears it", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("wifi-delivery-failed"));
    // At rest: no banner, the pill warns.
    await expect(wifiPill(page)).toHaveClass(/attention/);
    await expect(page.locator("sofabaton-wifi-commands-tab .delivery-panel")).toHaveCount(0);
    await expect(wifiPop(page)).toHaveCount(0);
    await wifiPill(page).click();
    await expect(wifiPop(page)).toContainText("The test command did not reach Home Assistant.");
    await expect(wifiPop(page)).toContainText("The hub calls this integration's HTTP listener at http://192.168.1.10:8060.");
    await expect(wifiPop(page)).toContainText("Allow TCP from the hub (192.168.1.110) to port 8060 on the Home Assistant host");
    await expect(wifiPop(page).locator("a")).toHaveAttribute("href", /networking\.md#optional--wifi-commands-and-wifi-events/);
    // An X2 with MQTT: the method choice sits under the status.
    await expect(wifiPop(page)).toContainText("Delivery method");
    await wifiPop(page).locator(".delivery-test-btn").click();
    await expect(wifiPop(page)).toContainText("Test command received (0.8 s).");
    await expect(wifiPop(page)).not.toContainText("did not reach");
    await expect(wifiPill(page)).not.toHaveClass(/attention/);
    const types = await wsTypes(page);
    expect(types).toContain("sofabaton_x1s/command_config/test_delivery");
    // An HTTP device never asks the hub for its broker link.
    expect(types).not.toContain("sofabaton_x1s/hub_mqtt/status");
    // Escape closes the dropdown.
    await page.keyboard.press("Escape");
    await expect(wifiPop(page)).toHaveCount(0);
  });

  test("an MQTT device whose hub has no broker says so in the dropdown and sets it up from there", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("wifi-delivery-mqtt-no-broker"));
    await expect(wifiPill(page)).toHaveClass(/attention/);
    // At rest only the stored record is read: no hub exchange.
    expect(await wsTypes(page)).not.toContain("sofabaton_x1s/hub_mqtt/status");
    await wifiPill(page).click();
    await expect(wifiPop(page)).toContainText("The hub is not connected to an MQTT broker.");
    await expect(wifiPop(page)).toContainText("Home Assistant's broker: core-mosquitto:1883.");
    // The broker problem leads; the failed test shrinks to one line.
    await expect(wifiPop(page)).not.toContainText("same broker Home Assistant uses");
    expect(await wsTypes(page)).toContain("sofabaton_x1s/hub_mqtt/status");
    await wifiPop(page).locator(".delivery-broker-setup").click();
    await expect(page.locator("sofabaton-wifi-commands-tab .broker-dialog")).toBeVisible();
    await page.locator("sofabaton-wifi-commands-tab .broker-connect").click();
    await expect(page.locator("sofabaton-wifi-commands-tab .broker-done")).toBeVisible();
    await page.locator("sofabaton-wifi-commands-tab .broker-done").click();
    await wifiPill(page).click();
    await expect(wifiPop(page)).not.toContainText("The hub is not connected to an MQTT broker.");
    await expect(wifiPop(page)).toContainText("The test command did not reach Home Assistant.");
  });

  test("a method picked in the Wifi Commands header waits for Sync, and Undo takes it back", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("wifi-delivery-failed"));
    await wifiPill(page).click();
    await wifiPop(page).locator(".delivery-option[data-transport='mqtt']").click();
    await expect(wifiPill(page)).toHaveClass(/mqtt pending/);
    await expect(wifiPop(page)).toContainText("Switches to MQTT on the next Sync. Until then, button presses keep using HTTP.");
    await expect(page.locator("sofabaton-wifi-commands-tab .detail-sync-btn")).toHaveClass(/sync-btn-primary/);
    const calls = await page.evaluate(() => window.__toolsCardHarness.getCalls().map((call) => call.message));
    expect(calls.some((m) => m?.type === "sofabaton_x1s/command_config/set_transport" && m.transport === "mqtt")).toBe(true);
    await wifiPop(page).locator(".delivery-undo").click();
    await expect(wifiPill(page)).not.toHaveClass(/pending/);
  });

  test("the Hub editor's pill on an X1S shows the path and Test without a method choice", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("device-editor-wifi-x1s"));
    await expect(editorPill(page)).toHaveClass(/attention/);
    await editorPill(page).click();
    await expect(editorPop(page)).toContainText("Allow TCP from the hub (192.168.1.110) to port 8060");
    await expect(editorPop(page).locator(".delivery-method")).toHaveCount(0);
    await editorPop(page).locator(".delivery-test-btn").click();
    await expect(editorPop(page)).toContainText("Test command received (0.8 s).");
  });

  test("the Hub editor's pill on an X2 picks MQTT as a pending edit for the next Sync", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("device-editor-wifi-transport"));
    await expect(editorPill(page)).not.toHaveClass(/attention/);
    await editorPill(page).click();
    await expect(editorPop(page)).toContainText("Test command received (0.6 s).");
    await expect(editorPop(page)).toContainText("5 minutes ago");
    await editorPop(page).locator(".delivery-option[data-transport='mqtt']").click();
    await expect(editorPill(page)).toHaveClass(/mqtt pending/);
    // The broker link was checked before the pick counted.
    expect(await wsTypes(page)).toContain("sofabaton_x1s/hub_mqtt/status");
  });

  test("MQTT waits for the hub's broker, and Set it up connects it with Home Assistant's login", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("wifi-create-mqtt-no-broker"));
    await page.waitForFunction(() => Boolean(window.__toolsCardHarness.getCard().shadowRoot
      ?.querySelector("sofabaton-wifi-commands-tab")?.shadowRoot?.querySelector(".transport-option")));
    const dialog = () => page.evaluate(() => {
      const root = window.__toolsCardHarness.getCard().shadowRoot.querySelector("sofabaton-wifi-commands-tab").shadowRoot;
      return {
        options: [...root.querySelectorAll(".transport-option-name")].map((node) => node.textContent.trim()),
        selected: root.querySelector(".transport-option.selected .transport-option-name")?.textContent?.trim(),
        check: root.querySelector(".transport-option-check")?.textContent?.trim() ?? "",
        createDisabled: Boolean(root.querySelector(".dialog-footer .dialog-btn-primary")?.disabled),
      };
    });
    // MQTT is preselected where it is offered, and its broker link is read at once.
    await expect.poll(async () => (await dialog()).check).toBe("The hub isn't connected to an MQTT broker. Set it up now?");
    expect(await dialog()).toMatchObject({ options: ["MQTT", "HTTP"], selected: "MQTT" });
    expect((await dialog()).createDisabled).toBe(true);
    await page.evaluate(() => {
      const root = window.__toolsCardHarness.getCard().shadowRoot.querySelector("sofabaton-wifi-commands-tab").shadowRoot;
      root.querySelector(".transport-setup-btn").click();
    });
    await page.waitForFunction(() => Boolean(window.__toolsCardHarness.getCard().shadowRoot
      .querySelector("sofabaton-wifi-commands-tab").shadowRoot.querySelector(".broker-dialog")));
    await page.evaluate(() => {
      window.__toolsCardHarness.getCard().shadowRoot.querySelector("sofabaton-wifi-commands-tab")
        .shadowRoot.querySelector(".broker-connect").click();
    });
    await page.waitForFunction(() => Boolean(window.__toolsCardHarness.getCard().shadowRoot
      .querySelector("sofabaton-wifi-commands-tab").shadowRoot.querySelector(".broker-done")));
    await page.evaluate(() => {
      window.__toolsCardHarness.getCard().shadowRoot.querySelector("sofabaton-wifi-commands-tab")
        .shadowRoot.querySelector(".broker-done").click();
    });
    await expect.poll(async () => (await dialog()).createDisabled).toBe(false);
    const setup = (await page.evaluate(() => window.__toolsCardHarness.getCalls()))
      .find((call) => call.message?.type === "sofabaton_x1s/hub_mqtt/setup");
    // Home Assistant's own login: the password never passes through the card.
    expect(setup.message).toEqual({
      type: "sofabaton_x1s/hub_mqtt/setup",
      entry_id: "hub-1",
      host: "192.168.1.10",
      port: 1883,
      use_ha_login: true,
    });
  });

  test("the X2 Settings tab shows the hub's broker, and Change warns before replacing it", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("settings-x2-broker"));
    const tile = () => page.evaluate(() =>
      window.__toolsCardHarness.getCard().shadowRoot?.querySelector(".setting-broker")?.textContent?.replace(/\s+/g, " ").trim() ?? "");
    await expect.poll(tile).toContain("The hub is not connected to an MQTT broker.");
    expect(await tile()).toContain("Home Assistant's broker: core-mosquitto:1883.");
    expect(await tile()).toContain("Set up");
    await page.evaluate(() => window.__toolsCardHarness.getCard().shadowRoot.querySelector(".setting-broker").click());
    await page.waitForFunction(() => Boolean(window.__toolsCardHarness.getCard().shadowRoot.querySelector(".broker-dialog")));
    // A disconnected hub may still have saved settings: the warning is always present.
    expect(await page.evaluate(() => Boolean(window.__toolsCardHarness.getCard().shadowRoot.querySelector(".broker-warning")))).toBe(true);
    await page.evaluate(() => window.__toolsCardHarness.getCard().shadowRoot.querySelector(".broker-connect").click());
    await page.waitForFunction(() => Boolean(window.__toolsCardHarness.getCard().shadowRoot.querySelector(".broker-done")));
    await page.evaluate(() => window.__toolsCardHarness.getCard().shadowRoot.querySelector(".broker-done").click());
    await expect.poll(tile).toContain("The hub is connected to an MQTT broker.");
    expect(await tile()).toContain("Change");
    await page.evaluate(() => window.__toolsCardHarness.getCard().shadowRoot.querySelector(".setting-broker").click());
    await page.waitForFunction(() => Boolean(window.__toolsCardHarness.getCard().shadowRoot.querySelector(".broker-warning")));
    expect(await page.evaluate(() =>
      window.__toolsCardHarness.getCard().shadowRoot.querySelector(".broker-warning").textContent.trim()))
      .toContain("This replaces any MQTT broker settings saved on the hub");
  });

  test("broker validation stays localized and identifies the invalid field", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html?width=360");
    await page.evaluate(async () => {
      await window.__toolsCardHarness.setLanguage("fr");
      await window.__toolsCardHarness.loadScenario("settings-x2-broker");
    });
    await page.locator(".setting-broker").click();
    const dialog = page.locator(".broker-dialog");
    await expect(dialog.locator(".broker-warning")).toBeVisible();
    const address = dialog.locator("#sb-broker-host input");
    await address.fill("999.999.999.999");
    await address.dispatchEvent("change");
    await dialog.locator(".broker-connect").click();
    await expect(dialog.locator("#sb-broker-host-error")).toHaveText("Saisissez une adresse IPv4 valide pour le broker.");
    await address.fill("192.168.1.10");
    await address.dispatchEvent("change");
    const port = dialog.locator("#sb-broker-port input");
    await port.fill("65536");
    await port.dispatchEvent("change");
    await dialog.locator(".broker-connect").click();
    await expect(dialog.locator("#sb-broker-port-error")).toHaveText("Saisissez un numéro de port compris entre 1 et 65535.");
    const calls = await page.evaluate(() => window.__toolsCardHarness.getCalls());
    expect(calls.some((call) => call.message?.type === "sofabaton_x1s/hub_mqtt/setup")).toBe(false);
  });

  test("the broker line is per hub: absent on another hub's tab, a hub row on the X2's own", async ({ page }) => {
    await page.goto("/tests/tools-card-harness.html");
    await page.evaluate(() => window.__toolsCardHarness.loadScenario("settings-x2-broker-other-hub"));
    const rows = () => page.evaluate(() =>
      [...window.__toolsCardHarness.getCard().shadowRoot.querySelectorAll(".settings-list .setting-tile")].map((row) => ({
        broker: row.classList.contains("setting-broker"),
        global: Boolean(row.querySelector(".setting-global-tag")),
        text: row.textContent.replace(/\s+/g, " ").trim(),
      })));
    await expect.poll(async () => (await rows()).length).toBeGreaterThan(0);
    // The X1S's tab has no broker line, and nothing asks the X2 for one.
    expect((await rows()).some((row) => row.broker)).toBe(false);
    const brokerCalls = () => page.evaluate(() =>
      window.__toolsCardHarness.getCalls().filter((call) => String(call.message?.type ?? "").startsWith("sofabaton_x1s/hub_mqtt/")).length);
    expect(await brokerCalls()).toBe(0);
    await page.evaluate(() => window.__toolsCardHarness.getCard()._store.selectHub("hub-x2"));
    await expect.poll(async () => (await rows()).find((row) => row.broker)?.text ?? "")
      .toContain("The hub is connected to an MQTT broker.");
    const tile = (await rows()).find((row) => row.broker);
    expect(tile.global).toBe(false);
    expect(tile.text).not.toContain("Basement X2");
    expect(tile.text).toContain("Change");
  });
});
