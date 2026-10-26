# Optional Diagral dashboard example

This opt-in, French-language example puts the alarm state, every reported fault,
groups and equipment on one page. It is not installed automatically by the
integration. It reads Home Assistant states only: no Diagral client, external
dependency, CDN, service call or alarm command is included in the custom card.
The separate native `alarm-panel` card handles alarm commands.

## Install through Home Assistant

1. Copy `diagral-overview-card.js` into your HA configuration directory at
   `www/diagral/diagral-overview-card.js`.
2. Add a dashboard resource with URL
   `/local/diagral/diagral-overview-card.js?v=1` and type **JavaScript module**.
   Dashboard resources are managed in **Settings > Dashboards > Resources**;
   enable Advanced Mode in your profile if this menu is hidden. Increment the
   version query when updating the file, then refresh your browser.
3. Copy `entity-mapping.example.json` to `entity-mapping.json` and replace every
   synthetic entity ID with an actual Diagral entity ID from your installation.
   Keep your existing alarm panel and anomalies sensor. Each list accepts an
   entity ID or an object containing `entity` and an optional display `name`.
4. Generate the dashboard with Python:

   ```sh
   python build-dashboard.py entity-mapping.json --output diagral-dashboard.yaml
   ```

5. Create an empty dashboard in HA, open its raw configuration editor and paste
   the generated configuration. The generated JSON is valid YAML. This does not
   require editing HA storage files or replacing any existing dashboard.

## Data and timing

The existing anomalies sensor retains its previous state/count semantics. The
card reads the additive `faults`, `fault_count`, `affected_device_count`,
`summary`, `last_success`, `updated_at` and `data_fresh` attributes. It displays
every fault of an affected device; unknown or unavailable data is never treated
as a current all-clear. Old readings retain their details with an explicit stale
label. Missing and implausibly future timestamps are identified as unknown.

The alarm panel supplies `arming_started_at`, `arming_ends_at`,
`arming_duration`, `arming_timing_known`, `arming_source` and
`arming_target_groups`. Configure the optional `arming_end_entity` to use the
corresponding timestamp sensor. When configured but unavailable, the card does
not fall back to a potentially stale timestamp on the panel.

A countdown appears only while the panel is `arming` and its timing is known.
The duration comes from the API group configuration, with no hardcoded 90-second
delay. It is labelled **estimated** because the start comes from an accepted HA
command. A delay discovered by polling or after a restart has an unknown origin,
so no new countdown is invented. At zero, the card waits for central confirmation
and never changes the backend state itself.

The default freshness threshold is 600 seconds and can be changed using
`stale_after_seconds`. One local timer updates an active countdown each second,
or otherwise wakes only when freshness expires. Detaching the card cancels it.
Names and fault messages are rendered as text, never evaluated as HTML. Clicking
a row opens the native HA more-info dialog.

## Offline checks

```sh
node test-card.mjs
python test-dashboard.py
```

The Node tests use built-in assertions, synthetic states and a minimal DOM stub.
They forbid network and backend commands and verify multiple faults, hostile
names, unknown and stale timing, freshness expiry and timer cleanup. The Python
tests validate explicit entity mappings and the generated native alarm card.
No physical arm/disarm operation is needed or performed by these tests.
