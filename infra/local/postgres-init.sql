-- Runs once when the local PostgreSQL container is first created.
-- "billing" is used by the running stack, "billing_test" by integration tests (it is wiped by the tests).
CREATE DATABASE billing_test OWNER billing;
