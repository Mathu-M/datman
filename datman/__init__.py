"""
datman. who dat?
"""
import datman.scanid
# If you remove this and dont manually import config before importing
# datman/dashboard.py you will get circular import errors. I am sooo sorry.
# We'd have to store hardcoded file paths in the dashboard database to
# otherwise fix this.
import datman.config
