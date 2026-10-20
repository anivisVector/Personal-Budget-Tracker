"""WSGI entrypoint.

Vercel detects the top-level `app` object in this file and serves it as a Python
function. Locally: `python app.py` (or `flask --app app run --debug`).
"""

import os

from budget_app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', '0') == '1', port=int(os.getenv('PORT', '5000')))
