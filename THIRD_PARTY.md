# Third-party software

FM2 is built on the open-source components below, each under its own licence (all compatible with
the AGPL-3.0-or-later licence of FM2). If your results depend on a method or library listed with a
reference, please cite it as well. The same list is shown in the app (Settings › Credits and license);
keep the two in step (`frontend/src/thirdParty.ts`).


## Methods and calculations

- **Modified Gompertz and logistic models** — published method. Growth-curve fits (λ, μmax, A).  
  Zwietering MH, Jongenburger I, Rombouts FM, van 't Riet K (1990). Modeling of the bacterial growth curve. Applied and Environmental Microbiology 56(6):1875–1881. doi:10.1128/aem.56.6.1875-1881.1990
- **Baranyi–Roberts model** — published method. Growth-curve fits with an explicit lag.  
  Baranyi J, Roberts TA (1994). A dynamic approach to predicting bacterial growth in food. International Journal of Food Microbiology 23(3–4):277–294. doi:10.1016/0168-1605(94)90157-0

## Python (server, analysis, figures)

- **SciPy** — BSD-3-Clause. Non-linear least squares for the growth fits, Welch t-test, ANOVA, Tukey HSD.  
  Virtanen P et al. (2020). SciPy 1.0: fundamental algorithms for scientific computing in Python. Nature Methods 17:261–272. doi:10.1038/s41592-019-0686-2
- **NumPy** — BSD-3-Clause. Numerical arrays and integration.  
  Harris CR et al. (2020). Array programming with NumPy. Nature 585:357–362. doi:10.1038/s41586-020-2649-2
- **pandas** — BSD-3-Clause. Tables and exports.  
  McKinney W (2010). Data structures for statistical computing in Python. Proceedings of the 9th Python in Science Conference, 56–61. doi:10.25080/Majora-92bf1922-00a
- **Matplotlib** — Matplotlib licence (PSF-based). Publication figures (PNG, SVG, PDF).  
  Hunter JD (2007). Matplotlib: a 2D graphics environment. Computing in Science & Engineering 9(3):90–95. doi:10.1109/MCSE.2007.55
- **FastAPI** — MIT. Web API.
- **Uvicorn** — BSD-3-Clause. Web server.
- **SQLAlchemy** — MIT. Database access (SQLite).
- **Pydantic** — MIT. Data validation.
- **openpyxl** — MIT. Excel exports.
- **HTTPX** — BSD-3-Clause. Notifications.
- **websockets** — BSD-3-Clause. Connection to the controllers.

## Web interface

- **Apache ECharts** — Apache-2.0. Interactive charts.  
  Li D et al. (2018). ECharts: a declarative framework for rapid construction of web-based visualization. Visual Informatics 2(2):136–146. doi:10.1016/j.visinf.2018.04.011
- **React and React Router** — MIT. User interface.
- **qrcode** — MIT. QR code for the phone link.
- **IBM Plex Sans and Plex Mono** — SIL Open Font License 1.1. Typefaces.
- **Vite and TypeScript** — MIT / Apache-2.0. Build tools (not part of the running software).
