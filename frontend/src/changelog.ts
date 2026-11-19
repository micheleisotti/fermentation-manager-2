/** Short changelog shown in Settings › Credits (newest first). Keep each line short. */
export const CHANGELOG: { version: string; date: string; items: string[] }[] = [
  { version: "2.2.1", date: "2026-11", items: [
    "Controller profiles can be edited; experiments using one keep their values and show when it changed",
    "Box-and-whisker plot of any parameter by condition in analysis sessions",
    "Figures exportable in black and white (line types, markers, hatching)",
    "Experiment names first, codes smaller; experiment list as rows on phones",
    "Compact header on the monitoring page; Plan buttons removed from monitoring and Home",
    "Dotted page, bolder main titles; themes Light, Cream, Sky, Dim, Dark (Grey became Sky)",
    "New button in dark blue; selection pills with a white background in every theme",
    "License changed to AGPL-3.0-or-later; How to cite (reference and BibTeX) and third-party software in Settings",
    "Exports and figure files record the software version",
    "Manual placeholder in Settings",
  ] },
  { version: "2.2.0", date: "2026-11", items: [
    "Estimated end of fermentation: dashed line in the charts and value in the analysis",
    "Buffer dosing: base and acid buffer (Addition 2) rates and cumulative volumes",
    "Controller panel redesigned: one Edit for target and limits",
    "Manual variables (OD, AFU…) selectable in the main chart",
    "Loops shown as actuators above / below the target",
    "Regulation: rule-based hints on the loop parameters",
    "Settings reordered; QR code to open the app on a phone; this changelog",
  ] },
  { version: "2.1.3", date: "2026-11", items: [
    "Regulation analysis: effective mean vs setpoint, time within tolerance, dosing episodes",
    "Loop on/off recorded at each reading",
  ] },
  { version: "2.1.2", date: "2026-11", items: [
    "Reactor monitoring page: setpoints, limits, loops and counters without an experiment",
    "Loops button with start/stop per loop; reset of the dose counters",
    "Home cards show every channel of the experiment; preview chart selectable",
  ] },
  { version: "2.1.1", date: "2026-11", items: [
    "Manual variables also after the end of a run (t since Start)",
    "Chart axes remembered; shorter event lists; branch shown next to the version",
    "Fixes: saving a plan with loop assignments, temperature dead band, legend colours",
  ] },
  { version: "2.1.0", date: "2026-11", items: [
    "Real ez2-Control driver: loop configuration, start/stop of loops, temperature control in profiles",
  ] },
];
