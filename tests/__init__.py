import faulthandler

# A test that hangs, such as a polling loop whose fake clock never moves on, fails the run and prints where it's stuck
# instead of stalling the pre-commit hook. The limit covers the whole run, including the integration tests.
faulthandler.dump_traceback_later(300, exit=True)
