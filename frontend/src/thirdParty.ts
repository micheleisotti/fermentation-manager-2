/**
 * Third-party software used by FM2, with licence and the reference to cite when a result depends on it.
 * Same content as THIRD_PARTY.md (keep both in step); versions are those of the lab PC at release time.
 */
export interface ThirdParty { name: string; licence: string; use: string; cite?: string }

export const THIRD_PARTY: { group: string; items: ThirdParty[] }[] = [
  {
    group: "Methods and calculations",
    items: [
      { name: "Modified Gompertz and logistic models", licence: "published method", use: "Growth-curve fits (λ, μmax, A).",
        cite: "Zwietering MH, Jongenburger I, Rombouts FM, van 't Riet K (1990). Modeling of the bacterial growth curve. Applied and Environmental Microbiology 56(6):1875–1881. doi:10.1128/aem.56.6.1875-1881.1990" },
      { name: "Baranyi–Roberts model", licence: "published method", use: "Growth-curve fits with an explicit lag.",
        cite: "Baranyi J, Roberts TA (1994). A dynamic approach to predicting bacterial growth in food. International Journal of Food Microbiology 23(3–4):277–294. doi:10.1016/0168-1605(94)90157-0" },
    ],
  },
  {
    group: "Python (server, analysis, figures)",
    items: [
      { name: "SciPy", licence: "BSD-3-Clause", use: "Non-linear least squares for the growth fits, Welch t-test, ANOVA, Tukey HSD.",
        cite: "Virtanen P et al. (2020). SciPy 1.0: fundamental algorithms for scientific computing in Python. Nature Methods 17:261–272. doi:10.1038/s41592-019-0686-2" },
      { name: "NumPy", licence: "BSD-3-Clause", use: "Numerical arrays and integration.",
        cite: "Harris CR et al. (2020). Array programming with NumPy. Nature 585:357–362. doi:10.1038/s41586-020-2649-2" },
      { name: "pandas", licence: "BSD-3-Clause", use: "Tables and exports.",
        cite: "McKinney W (2010). Data structures for statistical computing in Python. Proceedings of the 9th Python in Science Conference, 56–61. doi:10.25080/Majora-92bf1922-00a" },
      { name: "Matplotlib", licence: "Matplotlib licence (PSF-based)", use: "Publication figures (PNG, SVG, PDF).",
        cite: "Hunter JD (2007). Matplotlib: a 2D graphics environment. Computing in Science & Engineering 9(3):90–95. doi:10.1109/MCSE.2007.55" },
      { name: "FastAPI", licence: "MIT", use: "Web API." },
      { name: "Uvicorn", licence: "BSD-3-Clause", use: "Web server." },
      { name: "SQLAlchemy", licence: "MIT", use: "Database access (SQLite)." },
      { name: "Pydantic", licence: "MIT", use: "Data validation." },
      { name: "openpyxl", licence: "MIT", use: "Excel exports." },
      { name: "HTTPX", licence: "BSD-3-Clause", use: "Notifications." },
      { name: "websockets", licence: "BSD-3-Clause", use: "Connection to the controllers." },
    ],
  },
  {
    group: "Web interface",
    items: [
      { name: "Apache ECharts", licence: "Apache-2.0", use: "Interactive charts.",
        cite: "Li D et al. (2018). ECharts: a declarative framework for rapid construction of web-based visualization. Visual Informatics 2(2):136–146. doi:10.1016/j.visinf.2018.04.011" },
      { name: "React and React Router", licence: "MIT", use: "User interface." },
      { name: "qrcode", licence: "MIT", use: "QR code for the phone link." },
      { name: "IBM Plex Sans and Plex Mono", licence: "SIL Open Font License 1.1", use: "Typefaces." },
      { name: "Vite and TypeScript", licence: "MIT / Apache-2.0", use: "Build tools (not part of the running software)." },
    ],
  },
];
