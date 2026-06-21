# NBER Real Schema Fixtures

These files contain synthetic rows only. They preserve official NBER field
names from `Codebook.xlsx`, Stata-style DMY/DMYhms date encodings used by the
authors' scripts, blank missing values, status encodings, and the
`anon_product_id == 547957` sentinel that `load_csv_files.do` replaces with
missing.

`anon_bo_threads.csv` includes `src_cre_dt`, which is used by the released
authors' code but omitted from the codebook sheet. The exact raw CSV header
order for that extra column remains unresolved because raw datasets were not
downloaded for this prompt.
