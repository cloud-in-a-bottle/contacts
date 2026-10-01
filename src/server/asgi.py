from server.app import create_app

# The ASGI entry point hypercorn is pointed at.  Building the app here rather than in app.py keeps importing the
# module free of side effects, so tests can construct their own instance against a temporary database.
app = create_app()
