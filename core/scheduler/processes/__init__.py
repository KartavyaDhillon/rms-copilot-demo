"""Scheduler processes — one module per process, auto-imported by core.scheduler.catalog.

Files whose name starts with '_' are shared helpers, not processes.  ALL_NAMES is the agreed list (tests check it).
"""
ALL_NAMES = (
    "MCX WEB MORNING EMAIL", "SN327 WHATSAPP", "SN359 WHATSAPP", "PARTIC ID EMAIL",
    "CO & BO BLOCKING FILE", "3-9% BLOCKING FILES", "CONTRACT DELTA", "PERSISTENT NOISE CREATORS",
    "CM DELOI FILE", "SLBM REPORTS", "FO REPORTS", "MCX RESTRICTED BASKET", "CIRCUIT VALIDATION",
    "ATOM VS BANK ALERT", "MCX HOURLY SURVEILLANCE", "ACTIVE CLIENT REPORT", "NMASS DEPOSIT IMAGE",
    "CTCL DEPOSIT IMAGE", "ZONE WISE TOP 20 MARGIN", "PEAK MARGIN REPORT", "ALLOCATION REPORT",
)
