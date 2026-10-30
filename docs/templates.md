# Point templates: starter list and sources

The starter templates give each common kind of PI Point a reasonable starting point. They are not a
replacement for tuning: a single deviation copied across the tag database is the problem the tuner exists to
fix (the Savannah River Site study below found its tags "set uniformly at 1% span"). A template adds what the
signal alone can't tell us (the time limits, Step, whether to compress at all), a baseline to compare the
data-driven proposal against, and optional per-kind targets.

Every value can be changed, and templates can be added, turned off or removed, on the UI's Admin page.

## Rules the starter list follows

| Rule | Source |
|---|---|
| ExcDev is half of CompDev (the default when a template leaves ExcDev blank). | AVEVA 2023 ("Set ExcDev = ½ CompDev"); Savannah River Site 2003 (every example uses ExcDev = CompDev / 2) |
| CompDev no larger than the instrument's precision. | AVEVA 2023 ("Set CompDev <= Instrument precision") |
| CompMax of one shift, 8 hours (28,800 s), the same for most points. | Rockwell historian point-type guidance; AVEVA 2023 master table |
| ExcMax of 10 minutes (600 s). | AVEVA 2023 master table |
| CompMin and ExcMin 0 for points from interfaces that send exception reports. | Rockwell historian point-type guidance |
| 1% to 2% of span for most flows, pressures and levels; 1 to 2 degrees for temperatures. | Rockwell historian point-type guidance |
| Step on for discrete data: lab samples, batch weights, user inputs. Step off for continuous signals. | Rockwell historian point-type guidance |
| Compression off for laboratory and manually entered points. | Rockwell historian point-type guidance |
| Digital states: CompDev 0 (kept on every change), CompMax 1 day, ExcMax 8 hours. | AVEVA 2023 master table |

## Starter templates

The lowest priority number wins when several templates match. Units are compared loosely (°F, degF, deg F
and F are the same).

| Priority | Template | Matches | CompDev | ExcDev | Other | Basis |
|---|---|---|---|---|---|---|
| 10 | Digital and status | point type Digital | 0 | 0 | CompMax 1 d, ExcMax 8 h | AVEVA 2023 |
| 20 | Lab and manual entries | description: lab, laboratory, sample result, manual, manually entered, hand entered | 0 | 0 | Compression off, Step on | Rockwell |
| 30 | Setpoints and operator inputs | tag `*.SP`, `*_SP`, `*.SV`, `*.TARGET`, or description: setpoint, set point, target | 0 | 0 | Step on | Rockwell (user inputs: Step on); keeping every change is our choice |
| 40 | Totalizers and counters | description: total, totalizer, accumulated, counter, count | 0.1% of span | ½ CompDev | | Our choice: a running total moves steadily, so a tight band costs little |
| 50 | Valve and controller output | description: valve, position, output, opening, damper, and units % | 0.2 | 0.1 | | AVEVA 2023 lists ExcDev 0.1 for valve opening; CompDev 0.2 keeps ExcDev at half |
| 60 | pH | units pH | 0.1 | 0.05 | | AVEVA 2023 |
| 60 | Temperature, °F | units °F | 0.5 | 0.25 | | AVEVA 2023 |
| 60 | Temperature, °C or K | units °C, K | 0.3 | 0.15 | | The °F value in °C. AVEVA 2023 lists 1 °C; Rockwell says 1 to 2 degrees |
| 60 | Pressure, psi | units psi, psig, psia, psid | 0.5 | 0.25 | | AVEVA 2023 |
| 65 | Pressure, other units | units bar, kPa, MPa, inH2O, mmHg, ... | 1% of span | ½ CompDev | | Rockwell |
| 70 | Flow | description: flow, or a flow unit (gpm, lb/h, kg/h, m3/h, scfm, bpd, ...) | 1% of span | ½ CompDev | | Rockwell |
| 70 | Level | description: level, lvl | 1% of span | ½ CompDev | | Rockwell; Savannah River Site level examples at 1% of span |
| 80 | Electrical power | units MW, kW, MVAR, kVAR, MVA, kVA | 0.2 | 0.1 | | AVEVA 2023 |
| 80 | Electrical current | units A | 1 | 0.5 | | AVEVA 2023 |
| 80 | Voltage | units V | 2 | 1 | | AVEVA 2023 |
| 80 | Rotational speed | units rpm | 2 | 1 | | AVEVA 2023 |
| 80 | Vibration | units mils, µm, in/s, mm/s | 1% of span | ½ CompDev | | Our choice: AVEVA 2023 gives 0.2 mils for displacement only |
| 85 | Analyzer and composition | units ppm, ppb, mol%, %LEL, wt%, Btu/scf, ... | 0.5% of span | ½ CompDev | | Our choice: no published per-unit value |
| 1000 | Any other analog point (off) | point type Float16/32/64, Int16/32 | 0.5% of span | ½ CompDev | | Off by default; a catch-all is what tuning replaces |

Every template except Digital sets CompMax 8 h and ExcMax 10 min.

## Sources

- AVEVA, "Exception, Compression, and their Impacts on PI System Performance", AVEVA World San Francisco 2023
  (session UC23NA-3PGK04). Its "master table" for non-critical analog points gives CompDev and ExcDev by unit of
  measure. https://cdn.osisoft.com/osi/presentations/2023-AVEVA-San-Francisco/UC23NA-3PGK04-AVEVA_Bregenzer_Brent-Exception-Compression-and-their-Impacts-On-PI-System-Performance.pdf
- Savannah River Site, "PI Tag Tuning at the Savannah River Site", OSIsoft Users Conference 2003.
  https://cdn.osisoft.com/corp/en/media/presentations/uc/uc2003/363.pdf
- Rockwell Automation, "Historian Point Type parameters" (Studio 5000 Application Code Manager help), which
  follows the PI Data Archive's attribute guidance.
  https://www.rockwellautomation.com/en-in/docs/studio-5000-application-code-manager/5-01/application-code-manager-help-ditamap/project-development/historian-point-type-parameters.html
