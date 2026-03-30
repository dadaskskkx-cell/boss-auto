#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
streamlit run web.py --server.port 8501 --server.headless true
