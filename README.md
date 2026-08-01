# RMS Copilot

AI-powered Risk Management System (RMS) Copilot built using Python, FastAPI, Retrieval-Augmented Generation (RAG), and LLM Tool Calling.

## Overview

RMS Copilot is an intelligent assistant designed to help Risk Management System (RMS) teams automate operational workflows, retrieve compliance knowledge, and execute risk-monitoring processes through a conversational interface.

The system combines traditional RMS automation with AI-powered reasoning to assist users in performing daily risk-management tasks more efficiently.

---

## Key Features

### AI Agent

* Natural language interaction with RMS workflows
* Tool-calling architecture for executing RMS processes
* Context-aware responses using business rules and compliance knowledge

### Compliance Knowledge Assistant (RAG)

* Retrieval-Augmented Generation (RAG) over RMS circulars and SOPs
* Query exchange rules, margin requirements, payout processes, concentration limits, and collateral haircut guidelines
* Instant compliance and process-related assistance

### RMS Workflow Automation

Automated support for:

* Peak Margin Monitoring
* Margin Shortage Analysis (runs 9x/day, zone emails + WhatsApp margin alerts)
* CTCL vs NMASS Validation
* Concentration Monitoring
* MTF Order Review
* Payout Inspection
* CNC F11 Processing
* FTP Downloads
* Exchange File Downloads

### Dashboard

* Interactive web dashboard
* Live process execution monitoring
* AI chat interface
* Workflow execution controls
* Operational visibility for RMS teams

### Notifications

* Email notification support
* Workflow status reporting
* Alerting capabilities for operational tasks

---

## System Architecture

User
↓
Dashboard (HTML + JavaScript)
↓
FastAPI Backend
↓
RMS Copilot Agent
↓
Tool Layer
├── Peak Margin
├── Margin Shortage
├── Concentration
├── CTCL Validation
├── Payout Inspection
├── FTP Download
├── Exchange Downloads
└── Compliance RAG
↓
Data & Knowledge Base

---

## Technology Stack

### Backend

* Python
* FastAPI

### AI Layer

* Groq LLM Integration
* Retrieval-Augmented Generation (RAG)
* Tool Calling Architecture

### Data Processing

* Pandas
* Excel Processing
* CSV Processing

### Frontend

* HTML
* JavaScript
* Dashboard UI

### Integrations

* SMTP Email
* FTP Downloads
* NSE File Downloads

---

## Project Structure

```text
rms-copilot/

├── api.py
├── dashboard.html
├── requirements.txt

├── core/
│   ├── agent.py
│   ├── rag.py
│   ├── compliance.py
│   ├── penalty.py
│   ├── readiness.py
│   ├── llm.py
│   └── pipelines/

├── data/
│   └── circulars/

├── logs/

└── .env
```

---

## Example Queries

### Compliance Questions

* What is the Peak Margin penalty rule?
* Explain collateral haircut guidelines.
* What is the payout inspection process?

### Operational Questions

* Run concentration analysis.
* Run peak margin process.
* Execute CTCL validation.
* Show payout inspection workflow.

---

## Future Roadmap

* Daily RMS Summary Dashboard
* Root Cause Analysis Engine
* Risk Scoring Engine
* Predictive Risk Monitoring
* Historical Analytics
* Teams / WhatsApp Integration
* Management Dashboard
* Automated Alerting System

---

## Security

Sensitive information such as:

* API Keys
* SMTP Credentials
* FTP Credentials

are stored using environment variables and excluded from source control through `.gitignore`.

---

## Disclaimer

This repository is intended for educational, demonstration, and portfolio purposes. Any production deployment should implement organization-specific security, compliance, approval workflows, and operational controls.

---

## Author

Kartavya Dhillon

AI Automation | Python Development | Agentic AI Systems
