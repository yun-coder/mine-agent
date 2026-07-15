\set ON_ERROR_STOP on

BEGIN;

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION',
  :'app_user',
  :'app_pw'
)
WHERE NOT EXISTS (
  SELECT 1
  FROM pg_roles
  WHERE rolname = :'app_user'
)
\gexec

SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION',
  :'app_user',
  :'app_pw'
)
\gexec

SELECT format('ALTER DATABASE langfuse OWNER TO %I', :'app_user')
\gexec
SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_user')
\gexec

SELECT format(
  'ALTER TABLE %I.%I OWNER TO %I',
  namespace.nspname,
  object.relname,
  :'app_user'
)
FROM pg_class AS object
JOIN pg_namespace AS namespace ON namespace.oid = object.relnamespace
WHERE namespace.nspname = 'public'
  AND object.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
  AND pg_get_userbyid(object.relowner) <> :'app_user'
\gexec

SELECT format(
  'ALTER TYPE %I.%I OWNER TO %I',
  namespace.nspname,
  type.typname,
  :'app_user'
)
FROM pg_type AS type
JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace
WHERE namespace.nspname = 'public'
  AND type.typtype IN ('e', 'd')
  AND pg_get_userbyid(type.typowner) <> :'app_user'
\gexec

SELECT format(
  'ALTER FUNCTION %s OWNER TO %I',
  function.oid::regprocedure,
  :'app_user'
)
FROM pg_proc AS function
JOIN pg_namespace AS namespace ON namespace.oid = function.pronamespace
WHERE namespace.nspname = 'public'
  AND pg_get_userbyid(function.proowner) <> :'app_user'
\gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format(
  'GRANT CONNECT, TEMPORARY ON DATABASE langfuse TO %I',
  :'app_user'
)
\gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app_user')
\gexec
SELECT format(
  'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO %I',
  :'app_user'
)
\gexec
SELECT format(
  'GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO %I',
  :'app_user'
)
\gexec
SELECT format(
  'GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO %I',
  :'app_user'
)
\gexec

SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO %I',
  :'app_user',
  :'app_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO %I',
  :'app_user',
  :'app_user'
)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT ALL PRIVILEGES ON FUNCTIONS TO %I',
  :'app_user',
  :'app_user'
)
\gexec

COMMIT;
