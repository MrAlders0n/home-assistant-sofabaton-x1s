// The frame both editors run on (activity editor plan, decision 3): the HA
// card's live host (`tabs/activities-tab.ts`) recreated once for the device
// and the activity editor. It owns the load with its guards (firmware floor,
// an entity the cache never read in full, a missing entity), the baseline and
// the working copy, the draft slot, the leave ask with the card's "Unsynced
// changes" dialog, Sync as one followed job with the rebase, the stale and
// failed states, Reload from hub and the immediate whole-entity delete. The
// subclasses render the screen and name the routes.
//
// The editor follows the cache (2026-10-16, the HA card's model): when the
// hub's cache version moves past the snapshot it holds, a clean editor
// re-reads silently, and one with unsynced edits carries them over to the
// new snapshot (bundle-rebase.ts: the user's edit wins where both changed
// something, and one inline line says so). A stored draft from an older
// snapshot is carried over the same way when the editor opens, and a Sync
// refused because the snapshot moved (412) is carried over and sent again
// once. Deleted elsewhere: a clean editor closes, a dirty one shows the
// missing screen. Never while a job runs on the hub: mid job the cache
// passes through partial states (a command re-read clears the table before
// it refills), so the editor follows once, when the job has ended.
//
// Busy states are the card's: while this editor's own sync or delete runs,
// the screen is replaced by the full-panel progress view (never an overlay);
// a failed delete comes back as a banner over the re-opened editor; a job
// someone else started only locks Sync and Delete, and the dock narrates it.
//
// Offline mode (server panel backup plan, decision 6): the Backup tab's Edit
// section mounts the same editors on a loaded backup file, the card's
// `mode="backup"`. The host owns the bundle, the dirty flag and the edit
// session; the editor reports each edit with `sb-bundle-change` and closing
// with `sb-editor-close`, and nothing reaches the hub: no load, no draft
// slot, no Sync, and deleting the entity is a bundle edit like any other.

import { LitElement, html, nothing, type PropertyDeclarations, type PropertyValues, type TemplateResult } from "lit";
import { mdiAlertCircleOutline, mdiChip, mdiClose, mdiDatabaseRefreshOutline, mdiInformationOutline, mdiSyncAlert } from "@mdi/js";

import type { BackupBundleDevicePayload, BackupBundlePayload } from "../../../custom_components/sofabaton_x1s/www/src/shared/ha-context";
import { TOOLS_CARD_STRINGS } from "../../../custom_components/sofabaton_x1s/www/src/strings";
import { applyBundleDelete } from "../../../custom_components/sofabaton_x1s/www/src/tabs/backup-state";
import { rebaseBundle } from "../../../custom_components/sofabaton_x1s/www/src/tabs/bundle-rebase";
import { Coalescer, FOLLOW_DEBOUNCE_MS, FOLLOW_MAX_WAIT_MS, loadedVersion, needsReload, type LoadedVersion } from "../cache-follow";
import { jobStepMessage, renderOperationProgress } from "../components/operation-progress";
import { jobOutcomeText, problemText, type ApiResponse, type HubView, type JobView, type PanelApi, type RefreshScope, type SnapshotDocument } from "../panel-api";
import type { HubContext } from "../panel-context";
import { activeJob, firmwareFloor, type Gate } from "../panel-selectors";
import type { PanelStore } from "../panel-store";
import { elementsEqual, snapshotAsBundle } from "./device-editor-state";
import {
  draftSlot,
  entityDraftBase,
  entityDraftData,
  entityDraftScope,
  entityElement,
  entityListSub,
  rebaseDraftOnto,
  touchedDevices,
  withDraftData,
  type EntityElement,
  type EntityKind,
} from "./entity-editor-state";

export type EditorStage = "loading" | "guard_firmware" | "needs_refresh" | "missing" | "editing" | "sync_failed";

/** What adopting a fresh snapshot did (_adoptSnapshot). */
type AdoptOutcome = "same" | "gone" | "incomplete" | "rebased" | "clean";

/** The live host's wording, which the card parameterises by kind. */
export interface EntityFrameStrings {
  loading: string;
  back: string;
  firmwareUnsupportedTitle: string;
  firmwareUnsupportedBody: (installed: string | number, required: string | number) => string;
  needsRefreshTitle: string;
  needsRefreshBody: string;
  refreshEntity: string;
  missingTitle: string;
  missingBody: string;
  syncFailedTitle: string;
  syncStaleTitle: string;
  syncStaleBody: string;
  syncRetry: string;
  syncReload: string;
  syncKeepEditing: string;
  exitUnsyncedTitle: string;
  exitUnsyncedBody: string;
  exitSyncNow: string;
  exitWithoutSync: string;
}

export function icon(path: string, cls = ""): TemplateResult {
  return html`<svg class="mdi ${cls}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d=${path}></path></svg>`;
}

export abstract class SbPanelEntityEditor extends LitElement {
  static properties: PropertyDeclarations = {
    api: { attribute: false },
    ctx: { attribute: false },
    store: { attribute: false },
    offlineBundle: { attribute: false },
    offlineDirty: { attribute: false },
    _stage: { state: true },
    _snapshot: { state: true },
    _baseline: { state: true },
    _working: { state: true },
    _callbackDeviceId: { state: true },
    _exitConfirm: { state: true },
    _syncing: { state: true },
    _syncFailed: { state: true },
    _refreshing: { state: true },
    _deleting: { state: true },
    _deleteError: { state: true },
    _notice: { state: true },
    _changedElsewhere: { state: true },
  };

  api!: PanelApi;
  ctx: HubContext | null = null;
  store!: PanelStore;
  /** The loaded backup file this editor works on instead of the hub's snapshot (offline mode). */
  offlineBundle: BackupBundlePayload | null = null;
  /** The host's "edited since the file was loaded" flag; drives the Unsaved chip. */
  offlineDirty = false;

  protected _stage: EditorStage = "loading";
  protected _snapshot: SnapshotDocument | null = null;
  protected _baseline: BackupBundlePayload | null = null;
  protected _working: BackupBundlePayload | null = null;
  protected _callbackDeviceId: number | null = null;
  protected _exitConfirm: { then: () => void } | null = null;
  protected _syncing = false;
  protected _syncFailed: { stale: boolean; message: string } | null = null;
  protected _refreshing = false;
  protected _deleting = false;
  protected _deleteError: string | null = null;
  protected _notice: string | null = null;
  /** An edit of the user's was also changed elsewhere; the user's version was kept. */
  protected _changedElsewhere = false;
  /** The cache version the snapshot came from (cache-follow.ts). */
  private _loadedVersion: LoadedVersion | null = null;
  private _following = false;
  private readonly _follow = new Coalescer(() => void this._followCache(), { debounceMs: FOLLOW_DEBOUNCE_MS, maxWaitMs: FOLLOW_MAX_WAIT_MS });
  private _loadedKey: string | null = null;
  private _loadSeq = 0;
  /** The hub's gate when the last load started; anything but "pass" asks for another once it passes. */
  private _loadedGate: Gate | null = null;

  // -- what a subclass names ------------------------------------------------------------------------

  protected abstract readonly entityKind: EntityKind;
  protected abstract get entityId(): number | null;
  protected abstract get frameStrings(): EntityFrameStrings;
  /** Drop the screen's own transient state (dialogs, sub-views) on an entity switch. */
  protected abstract _resetView(): void;
  protected abstract _renderEditing(): TemplateResult;
  /** The single-entity write; `devices` are the device elements an activity edit touched. */
  protected abstract _startSync(hubId: string, entityId: number, element: EntityElement, devices: BackupBundleDevicePayload[], snapshotId: string): Promise<ApiResponse<JobView>>;
  protected abstract _startDelete(hubId: string, entityId: number): Promise<ApiResponse<JobView>>;

  /** A write that must land before the entity's own (the activity editor's Wifi Events phase); false stops the sync. */
  protected async _beforeSync(_hubId: string): Promise<boolean> {
    return true;
  }

  // -- lifecycle ---------------------------------------------------------------------------------------

  protected get _offline(): boolean {
    return this.offlineBundle !== null;
  }

  protected willUpdate(_changed: PropertyValues): void {
    if (!this._offline) return;
    // The host's bundle is the working copy; there is no baseline to diff against.
    const key = `offline:${this.entityId ?? ""}`;
    if (key !== this._loadedKey) {
      this._loadedKey = key;
      this._exitConfirm = null;
      this._syncFailed = null;
      this._notice = null;
      this._resetView();
    }
    this._snapshot = null;
    this._callbackDeviceId = null;
    this._baseline = this.offlineBundle;
    this._working = this.offlineBundle;
    this._stage = this.entityId != null && entityElement(this.offlineBundle, this.entityKind, this.entityId) ? "editing" : "missing";
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this._follow.cancel();
  }

  protected updated(changed: PropertyValues): void {
    if (this._offline) return;
    const key = `${this.ctx?.hub?.hub_id ?? ""}:${this.entityId ?? ""}`;
    if (key !== this._loadedKey) {
      this._loadedKey = key;
      this._reset();
      if (this.ctx?.hub && this.entityId != null) void this._load();
    } else if (changed.has("ctx") && this._stage === "missing" && this.ctx?.gate === "pass" && this._loadedGate !== "pass") {
      // The snapshot was asked for while the hub could not answer (disabled: a 409), or the read
      // failed in transit (_loadedGate reset to null, CR-F5b-2): read again.
      this._stage = "loading";
      this._notice = null;
      void this._load();
    } else if (changed.has("ctx") && this._stage === "guard_firmware" && !firmwareFloor(this._hub)) {
      // The hub reported updated firmware: the guard lifts by itself, as the card's does.
      this._stage = "loading";
      void this._load();
    } else if (changed.has("ctx") && this._stage === "editing" && this._dirty && !this.ctx?.runtime?.draft) {
      // The dock's Discard dropped the draft: the working copy follows.
      this._working = this._baseline ? structuredClone(this._baseline) : null;
      this._changedElsewhere = false;
    }
    if (changed.has("ctx")) this._maybeFollowCache();
  }

  private _reset(): void {
    this._stage = "loading";
    this._snapshot = null;
    this._baseline = null;
    this._working = null;
    this._exitConfirm = null;
    this._syncing = false;
    this._refreshing = false;
    this._deleting = false;
    this._syncFailed = null;
    this._deleteError = null;
    this._notice = null;
    this._changedElsewhere = false;
    this._loadedVersion = null;
    this._follow.cancel();
    this._resetView();
  }

  /** True while the element still shows the entity a job started on: the
   *  cached element is reused for every entity, and a job's continuation
   *  must not act on the next one (CR-F5b-1). */
  private _stillOn(key: string | null): boolean {
    return this._loadedKey === key;
  }

  /** A job this editor did not start holds the hub (the card's `hubCommandBusy`): Sync and Delete wait for it. */
  protected get _hubBusy(): boolean {
    return !this._offline && this.ctx !== null && !this.ctx.free;
  }

  protected get _hub(): HubView | null {
    return this.ctx?.hub ?? null;
  }

  protected get _hubVersion(): string | null {
    // A backup file is edited by its own hub model's rules (names, buttons), whatever hub is selected.
    if (this._offline) return this._working?.hub?.version ?? null;
    return this._hub?.status?.hub_version ?? this._hub?.config?.hub_version ?? this._working?.hub?.version ?? null;
  }

  // -- loading: the snapshot, the callback device; the firmware floor is the server's verdict on the hub ---

  protected async _load(options: { keepDraft?: boolean } = {}): Promise<void> {
    const hubId = this._hub?.hub_id;
    const entityId = this.entityId;
    const kind = this.entityKind;
    if (!hubId || entityId == null) return;
    const seq = ++this._loadSeq;
    this._loadedGate = this.ctx?.gate ?? null;
    const versionAtStart = this.ctx?.hub?.cache ?? null;
    let snapshot: ApiResponse<SnapshotDocument>;
    let callback: ApiResponse<{ device_id: number | null }> | null;
    try {
      [snapshot, callback] = await Promise.all([
        this.api.snapshot(hubId),
        this.api.request<{ device_id: number | null }>("GET", `hubs/${encodeURIComponent(hubId)}/callback-device`).catch(() => null),
      ]);
    } catch (err) {
      // A failure in transit (the server restarting, a Wi-Fi blip): say so,
      // and read again on the next state update (CR-F5b-2).
      if (seq !== this._loadSeq) return;
      this._notice = String(err);
      this._stage = "missing";
      this._loadedGate = null;
      return;
    }
    if (seq !== this._loadSeq) return;
    this._callbackDeviceId = callback?.ok && callback.body && typeof callback.body.device_id === "number" ? callback.body.device_id : null;
    if (!snapshot.ok || !snapshot.body) {
      this._notice = problemText(snapshot);
      this._stage = "missing";
      return;
    }
    this._snapshot = snapshot.body;
    this._loadedVersion = loadedVersion(versionAtStart, snapshot.body);
    if (firmwareFloor(this._hub)) {
      this._stage = "guard_firmware";
      return;
    }
    const bundle = snapshotAsBundle(snapshot.body);
    const element = entityElement(bundle, kind, entityId);
    if (!element) {
      this._stage = "missing";
      return;
    }
    if (element.complete === false) {
      this._baseline = bundle;
      this._working = null;
      this._stage = "needs_refresh";
      return;
    }
    this._baseline = bundle;
    this._stage = "editing";
    this._syncFailed = null;
    if (options.keepDraft === false) this._changedElsewhere = false;
    // A stored draft for this entity goes back into the working copy: as it
    // is when it was taken from this very snapshot, carried over to it
    // otherwise (the hub moved on while the page was closed). Never a question.
    const draft = options.keepDraft !== false ? this.ctx?.runtime?.draft ?? null : null;
    const data = entityDraftData(draft, kind, entityId);
    if (!draft || !data) {
      this._working = structuredClone(bundle);
      return;
    }
    if (draft.snapshotId === snapshot.body.snapshot_id) {
      this._working = withDraftData(bundle, kind, entityId, data);
      return;
    }
    const carried = rebaseDraftOnto(bundle, kind, entityId, data, entityDraftBase(draft, kind, entityId));
    this._working = structuredClone(bundle);
    if (carried.overlap) this._changedElsewhere = true;
    // Stored again against this snapshot (or dropped when nothing is left).
    this._commit(carried.bundle);
  }

  // -- following the cache ---------------------------------------------------------------------------

  /** A newer cache version than the snapshot held: read it again soon (coalesced). Never mid sync, delete or reload: those end in their own read.
   *  Nor while a job runs on the hub: the next hub update after its end (updated() re-checks on every one) brings the follow. */
  private _maybeFollowCache(): void {
    if (this._offline || this._following || this._stage !== "editing" || this._syncing || this._deleting || this._refreshing) return;
    if (activeJob(this._hub)) return;
    if (!needsReload(this._loadedVersion, this.ctx?.hub?.cache)) return;
    this._follow.poke();
  }

  private async _followCache(): Promise<void> {
    const hubId = this._hub?.hub_id;
    if (!hubId || this._offline || this._following || this._stage !== "editing" || this._syncing || this._deleting || this._refreshing) return;
    // A job started since the poke: wait for its end.
    if (activeJob(this._hub)) return;
    const key = this._loadedKey;
    const versionAtStart = this.ctx?.hub?.cache ?? null;
    this._following = true;
    let read = false;
    try {
      const response = await this.api.snapshot(hubId);
      if (!this._stillOn(key) || this._stage !== "editing" || this._syncing || this._deleting || this._refreshing) return;
      // A job started while this was read: the rows may be a partial state; its end follows again.
      if (activeJob(this._hub)) return;
      if (!response.ok || !response.body) return;
      read = true;
      const outcome = this._adoptSnapshot(response.body, versionAtStart);
      if (outcome === "gone") {
        // Deleted elsewhere: a clean editor has nothing left to show; unsynced edits cannot land any more.
        if (!this._differs) {
          this._goToList();
        } else {
          this.store.discardDraft(hubId);
          this._notice = null;
          this._stage = "missing";
        }
      }
    } catch {
      // the next report tries again
    } finally {
      this._following = false;
      // A newer version may have been reported while this one was read.
      if (read && this._stillOn(key)) this._maybeFollowCache();
    }
  }

  /**
   * Make a fresh snapshot the baseline and carry the user's edits over to it.
   * "same": nothing moved; "gone": the entity is not in it any more;
   * "incomplete": its detail is being read again (the next version brings it);
   * "rebased" / "clean": adopted, with or without edits left to sync.
   */
  private _adoptSnapshot(doc: SnapshotDocument, versionAtStart: HubView["cache"] | null): AdoptOutcome {
    const entityId = this.entityId;
    if (entityId == null || !this._baseline || !this._working) return "same";
    this._loadedVersion = loadedVersion(versionAtStart, doc);
    // Nothing moved: no carry-over, and a refused write is not worth sending again.
    if (this._snapshot && doc.snapshot_id === this._snapshot.snapshot_id) return "same";
    const fresh = snapshotAsBundle(doc);
    const element = entityElement(fresh, this.entityKind, entityId);
    if (!element) return "gone";
    if (element.complete === false) return "incomplete";
    const dirty = this._differs;
    this._snapshot = doc;
    if (!dirty) {
      this._baseline = fresh;
      this._working = structuredClone(fresh);
      return "clean";
    }
    const { bundle, overlap } = rebaseBundle(this._baseline, this._working, fresh);
    this._baseline = fresh;
    if (overlap) this._changedElsewhere = true;
    this._commit(bundle);
    return this._differs ? "rebased" : "clean";
  }

  /** Read the current snapshot and carry the edits over (a refused or failed sync); the outcome, or null when the read failed. */
  private async _rebaseOntoCurrent(hubId: string, key: string | null): Promise<AdoptOutcome | null> {
    const versionAtStart = this.ctx?.hub?.cache ?? null;
    try {
      const response = await this.api.snapshot(hubId);
      if (!this._stillOn(key) || !response.ok || !response.body) return null;
      return this._adoptSnapshot(response.body, versionAtStart);
    } catch {
      return null;
    }
  }

  // -- the working copy ------------------------------------------------------------------------------------

  protected get _workingEntity(): EntityElement | null {
    return this.entityId != null ? entityElement(this._working, this.entityKind, this.entityId) : null;
  }

  protected get _baselineEntity(): EntityElement | null {
    return this.entityId != null ? entityElement(this._baseline, this.entityKind, this.entityId) : null;
  }

  /** Device elements the edit touched: only an activity edit reaches into devices (Set input). */
  protected _touchedDevices(working: BackupBundlePayload | null = this._working): BackupBundleDevicePayload[] {
    return this.entityKind === "activity" ? touchedDevices(working, this._baseline) : [];
  }

  /** Dirty is JSON inequality of the entity's element, as on the card (whole-bundle compare there). */
  protected get _dirty(): boolean {
    if (this._offline) return this.offlineDirty;
    if (this._stage !== "editing") return false;
    return this._differs;
  }

  /** The working copy holds edits the hub does not have, whatever the stage (a failed sync's included). */
  private get _differs(): boolean {
    if (!this._working || !this._baseline) return false;
    return !elementsEqual(this._workingEntity, this._baselineEntity) || this._touchedDevices().length > 0;
  }

  /** The card's `_commitEditBundleEdit`: replace the working bundle, mirror the edit into the draft slot. */
  protected _commit(next: BackupBundlePayload): void {
    if (this._offline) {
      this._working = next;
      this.dispatchEvent(new CustomEvent("sb-bundle-change", { bubbles: true, composed: true, detail: { bundle: next } }));
      return;
    }
    const hubId = this._hub?.hub_id;
    const entityId = this.entityId;
    if (!hubId || entityId == null || !this._snapshot) return;
    this._working = next;
    const element = entityElement(next, this.entityKind, entityId);
    const devices = this._touchedDevices(next);
    const slot = element && (!elementsEqual(element, this._baselineEntity) || devices.length) ? draftSlot(next, this._baseline, this.entityKind, entityId) : null;
    if (slot) {
      this.store.setDraft(hubId, {
        scope: entityDraftScope(this.entityKind, entityId),
        snapshotId: this._snapshot.snapshot_id,
        data: slot.data,
        base: slot.base,
      });
    } else {
      this.store.discardDraft(hubId);
    }
  }

  /** For the shell: leaving with unsynced edits goes through the card's dialog (device editor plan, decision 3). */
  hasUnsyncedChanges(): boolean {
    // Offline edits live in the host's edit session: leaving loses nothing.
    return !this._offline && this._dirty;
  }

  askToLeave(then: () => void): void {
    if (!this.hasUnsyncedChanges()) {
      then();
      return;
    }
    if (this._syncing || this._deleting) {
      // The progress view has no room for the dialog; the move would be
      // swallowed and the dialog pop up after the job (CR-F5b-3).
      this._dockError("Wait for the sync to finish before leaving.");
      return;
    }
    this._exitConfirm = { then };
  }

  protected _requestClose = (): void => {
    this.askToLeave(() => this._goToList());
  };

  protected _goToList = (): void => {
    if (this._offline) {
      this.dispatchEvent(new CustomEvent("sb-editor-close", { bubbles: true, composed: true }));
      return;
    }
    this.dispatchEvent(new CustomEvent("sb-navigate", { bubbles: true, composed: true, detail: { tab: "hub", sub: entityListSub(this.entityKind) } }));
  };

  /** A failure shown in the bottom dock (the shell's sb-message) where it is always in view. */
  protected _dockError(text: string): void {
    this.dispatchEvent(new CustomEvent("sb-message", { bubbles: true, composed: true, detail: { text, ok: false } }));
  }

  /** The offset the sticky top dock and the sticky header take from the viewport's top. */
  protected _stickyOffset(): number {
    const dock = parseFloat(getComputedStyle(this).getPropertyValue("--top-dock-height")) || 0;
    const header = this.renderRoot.querySelector<HTMLElement>(".sticky-header")?.getBoundingClientRect().height ?? 0;
    return dock + header;
  }

  private _leaveWithoutSync = (): void => {
    const then = this._exitConfirm?.then;
    this._exitConfirm = null;
    const hubId = this._hub?.hub_id;
    if (hubId) this.store.discardDraft(hubId);
    if (this._baseline) this._working = structuredClone(this._baseline);
    this._changedElsewhere = false;
    then?.();
  };

  private _syncAndLeave = (): void => {
    const then = this._exitConfirm?.then;
    this._exitConfirm = null;
    void this._sync().then((ok) => {
      if (ok) then?.();
    });
  };

  // -- sync: the single-entity PUT with If-Match, followed as a job ------------------------------------------

  private _failSync(stale: boolean, message: string): false {
    this._syncFailed = { stale, message };
    this._stage = "sync_failed";
    return false;
  }

  /** A started job followed to its end; the failure text when it did not finish, null when it did. */
  protected async _followToEnd(hubId: string, started: ApiResponse<JobView>): Promise<{ stale: boolean; message: string } | null> {
    if (started.status === 412) return { stale: true, message: problemText(started) };
    if (started.status !== 202 || !started.body) {
      this.store.noteResponse(hubId, started);
      return { stale: false, message: problemText(started) };
    }
    const job = await this.api.followJob(hubId, started.body.job_id);
    if (job && job.status === "done") return null;
    const message = jobOutcomeText(job) ?? "Did not finish";
    const stale = Boolean(job?.error && /stale|outdated/i.test(`${job.error.type} ${job.error.detail ?? ""}`));
    return { stale, message };
  }

  protected async _sync(): Promise<boolean> {
    const hubId = this._hub?.hub_id;
    const entityId = this.entityId;
    if (!hubId || entityId == null || !this._workingEntity || !this._snapshot || this._syncing) return false;
    // Nothing to write (a leave dialog's Sync now after a sync already ran): done (CR-F5b-3).
    if (!this._dirty) return true;
    const key = this._loadedKey;
    this._syncing = true;
    this._syncFailed = null;
    this._deleteError = null;
    this._changedElsewhere = false;
    this._follow.cancel();
    try {
      if (!(await this._beforeSync(hubId))) return false;
      let failed: { stale: boolean; message: string } | null = null;
      for (let attempt = 0; ; attempt++) {
        if (!this._stillOn(key)) return false;
        // _beforeSync (or the carry-over below) may have moved the snapshot and the working copy: read them now.
        const element = this._workingEntity;
        const snapshot = this._snapshot;
        if (!element || !snapshot) return false;
        const started = await this._startSync(hubId, entityId, element, this._touchedDevices(), snapshot.snapshot_id);
        if (started.status === 412 && attempt === 0 && this._stillOn(key)) {
          // The cache moved between the last follow and this write: carry the
          // edit over to the current snapshot and send it again, once.
          const outcome = await this._rebaseOntoCurrent(hubId, key);
          if (outcome === "rebased") continue;
          if (outcome === "clean") break;        // what the edit wanted is there already
          if (outcome === "gone") return this._failSync(false, this.frameStrings.missingBody);
        }
        failed = await this._followToEnd(hubId, started);
        break;
      }
      // The user moved to another entity meanwhile: its draft and stage are its own (CR-F5b-1).
      if (!this._stillOn(key)) return !failed;
      if (failed) {
        this._failSync(failed.stale, failed.message);
        // Part of it may have landed (the server reads the entity back before
        // it reports the failure): carry the edit over to that, so a retry
        // writes only what is missing and passes the snapshot check.
        await this._rebaseOntoCurrent(hubId, key);
        return false;
      }
      // Success: rebase from the hub's new snapshot; the draft is spent.
      this._exitConfirm = null;
      this.store.discardDraft(hubId);
      await this._load({ keepDraft: false });
      return true;
    } catch (err) {
      return this._stillOn(key) ? this._failSync(false, String(err)) : false;
    } finally {
      if (this._stillOn(key)) {
        this._syncing = false;
        // A change reported while the sync ran, after its own read.
        this._maybeFollowCache();
      }
    }
  }

  /** The dock's Sync button: the header's sync, or the retry while a failed sync is on screen. */
  syncFromDock(): void {
    if (this._stage === "sync_failed") this._retrySync();
    else if (this._stage === "editing") void this._sync();
  }

  protected _failBeforeSync(stale: boolean, message: string): false {
    return this._failSync(stale, message);
  }

  private _retrySync = (): void => {
    this._stage = "editing";
    this._syncFailed = null;
    // Everything landed after all (the carry-over found nothing left to write): back to editing.
    if (!this._differs) return;
    void this._sync();
  };

  private _keepEditing = (): void => {
    this._stage = "editing";
    this._syncFailed = null;
  };

  /** "Reload from hub": read this entity from the hub as a job, then re-open it; the local edit is discarded. */
  protected async _reloadFromHub(): Promise<void> {
    const hubId = this._hub?.hub_id;
    const entityId = this.entityId;
    if (!hubId || entityId == null || this._refreshing) return;
    const key = this._loadedKey;
    this._refreshing = true;
    try {
      this.store.discardDraft(hubId);
      const scope: RefreshScope = this.entityKind === "device" ? { device_id: entityId } : { activity_id: entityId };
      const started = await this.api.refreshSnapshot(hubId, scope);
      if (started.status === 202 && started.body) await this.api.followJob(hubId, started.body.job_id);
      else this.store.noteResponse(hubId, started);
    } finally {
      if (this._stillOn(key)) this._refreshing = false;
    }
    if (this._stillOn(key)) {
      await this._load({ keepDraft: false });
      if (this._stillOn(key)) this._maybeFollowCache();
    }
  }

  // -- delete the entity (immediate, a job) ---------------------------------------------------------------------

  protected async _deleteEntity(): Promise<void> {
    if (this._offline) {
      // The card's backup mode: the entity leaves the file with its cascade; the hub only sees it on an erasing restore.
      const id = this.entityId;
      if (id == null || !this._working) return;
      this._commit(applyBundleDelete(this._working, this.entityKind === "device" ? { kind: "device", deviceId: id } : { kind: "activity", activityId: id }));
      this._goToList();
      return;
    }
    const hubId = this._hub?.hub_id;
    const entityId = this.entityId;
    if (!hubId || entityId == null || this._deleting) return;
    const key = this._loadedKey;
    this._deleting = true;
    this._deleteError = null;
    try {
      const started = await this._startDelete(hubId, entityId);
      if (!this._stillOn(key)) return;
      if (started.status !== 202 || !started.body) {
        this.store.noteResponse(hubId, started);
        this._deleteError = `Delete refused: ${problemText(started)}`;
        return;
      }
      const job = await this.api.followJob(hubId, started.body.job_id);
      if (!this._stillOn(key)) return;
      if (!job || job.status !== "done") {
        this._deleteError = `Delete failed: ${job?.error?.detail || jobOutcomeText(job)}`;
        return;
      }
      this.store.discardDraft(hubId);
      this._goToList();
    } catch (err) {
      if (this._stillOn(key)) this._deleteError = `Delete failed: ${String(err)}`;
    } finally {
      if (this._stillOn(key)) this._deleting = false;
    }
  }

  // -- render ---------------------------------------------------------------------------------------------------

  render(): TemplateResult {
    const S = this.frameStrings;
    if ((!this._hub && !this._offline) || this.entityId == null) return html`<div class="panel"><div class="hint">Pick a hub above.</div></div>`;
    // The card's syncing and deleting stages: the progress view stands in for the editor while its own job runs.
    const C = TOOLS_CARD_STRINGS.activities;
    if (this._deleting) return this._renderProgress("editor-deleting", C.deletingTitle(this.entityKind), C.deletingMessage(this.entityKind));
    if (this._syncing) return this._renderProgress("editor-syncing", C.syncingTitle, jobStepMessage(activeJob(this._hub)) || C.syncingMessage);
    switch (this._stage) {
      case "loading":
        return html`<div class="panel"><div class="capture-error"><div class="guard-sub">${S.loading}</div></div></div>`;
      case "guard_firmware": {
        const floor = firmwareFloor(this._hub);
        return this._renderGuard(mdiChip, S.firmwareUnsupportedTitle, S.firmwareUnsupportedBody(floor?.installed ?? "?", floor?.required ?? "?"), html`<button class="btn" @click=${this._goToList}>${S.back}</button>`, "guard-firmware");
      }
      case "needs_refresh":
        return this._renderGuard(mdiDatabaseRefreshOutline, S.needsRefreshTitle, S.needsRefreshBody, html`
          <button class="btn btn-primary" id="editor-refresh" ?disabled=${this._refreshing} @click=${() => void this._reloadFromHub()}>${this._refreshing ? "Refreshing…" : S.refreshEntity}</button>
          <button class="btn" @click=${this._goToList}>${S.back}</button>`, "guard-refresh");
      case "missing":
        return this._renderGuard(mdiAlertCircleOutline, S.missingTitle, this._notice ?? S.missingBody, html`<button class="btn" @click=${this._goToList}>${S.back}</button>`, "guard-missing");
      case "sync_failed": {
        const failed = this._syncFailed;
        const stale = Boolean(failed?.stale);
        return this._renderGuard(stale ? mdiSyncAlert : mdiAlertCircleOutline, stale ? S.syncStaleTitle : S.syncFailedTitle, stale ? S.syncStaleBody : failed?.message ?? "", html`
          ${stale ? nothing : html`<button class="btn btn-primary" id="editor-retry" @click=${this._retrySync}>${S.syncRetry}</button>`}
          <button class="btn" id="editor-reload" ?disabled=${this._refreshing} @click=${() => void this._reloadFromHub()}>${this._refreshing ? "Reloading…" : S.syncReload}</button>
          <button class="btn" id="editor-keep-editing" @click=${this._keepEditing}>${S.syncKeepEditing}</button>`, "sync-failed");
      }
      default:
        return this._renderEditing();
    }
  }

  private _renderProgress(id: string, title: string, message: string): TemplateResult {
    return html`<div class="tab-panel">${renderOperationProgress({ id, mode: "restore", title, message })}</div>`;
  }

  /** The lines over the editor: the card's delete-error banner (a failed delete
   *  re-opens the editor with the reason on top) and the card's one line when
   *  an edit of the user's was also changed elsewhere (no button). */
  protected _renderEditorBanners(): TemplateResult {
    return html`
      ${this._deleteError
        ? html`<div class="notice-banner notice-banner--error" id="editor-delete-error" role="alert">${icon(mdiAlertCircleOutline)}<span>${this._deleteError}</span><button class="notice-banner-btn" type="button" @click=${() => { this._deleteError = null; }}>Dismiss</button></div>`
        : nothing}
      ${this._changedElsewhere && !this._offline
        ? html`<div class="notice-banner notice-banner--info" id="editor-changed-elsewhere">${icon(mdiInformationOutline)}<span>${TOOLS_CARD_STRINGS.activities.changedElsewhere(this.entityKind)}</span></div>`
        : nothing}`;
  }

  protected _renderGuard(iconPath: string, title: string, body: string, actions: TemplateResult, id: string): TemplateResult {
    return html`
      <div class="tab-panel tab-panel--detail">
        <div class="detail-view" id=${id}>
          <div class="capture-error">
            <div class="guard-icon">${icon(iconPath)}</div>
            <div class="capture-error-title">${title}</div>
            <div class="guard-sub">${body}</div>
            <div class="action-row">${actions}</div>
          </div>
        </div>
      </div>
    `;
  }

  protected _renderExitConfirmDialog(): TemplateResult | typeof nothing {
    if (!this._exitConfirm) return nothing;
    const S = this.frameStrings;
    const close = () => { this._exitConfirm = null; };
    return html`
      <div class="modal-backdrop" @click=${close}>
        <div class="dialog small" id="exit-dialog" @click=${(event: Event) => event.stopPropagation()}>
          <div class="dialog-header"><div class="dialog-title">${S.exitUnsyncedTitle}</div><button class="dialog-close" type="button" aria-label=${S.syncKeepEditing} @click=${close}>${icon(mdiClose)}</button></div>
          <div class="dialog-body"><div class="dialog-text">${S.exitUnsyncedBody}</div></div>
          <div class="dialog-footer">
            <button class="btn btn-danger" id="exit-leave" type="button" @click=${this._leaveWithoutSync}>${S.exitWithoutSync}</button>
            <div class="dialog-footer-actions">
              <button class="btn" id="exit-keep" type="button" @click=${close}>${S.syncKeepEditing}</button>
              <button class="btn btn-primary" id="exit-sync" type="button" @click=${this._syncAndLeave}>${S.exitSyncNow}</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }
}
