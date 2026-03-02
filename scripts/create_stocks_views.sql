-- Stocks helper views for MCP-friendly querying.
--
-- Purpose:
-- - Enable retrieving business summaries filtered by `industry` without requiring joins in MCP clients.
-- - The MCP can then call:
--     query_table(schema="stocks", table="symbol_industry_business_summary", where={"industry":"Biotechnology"}, ...)
--
-- Run this as a MySQL user with CREATE VIEW privileges on the `stocks` schema.

CREATE OR REPLACE VIEW `stocks`.`symbol_industry_business_summary` AS
SELECT
  s.`symbol` AS `symbol`,
  s.`sector` AS `sector`,
  s.`industry` AS `industry`,
  b.`business_summary` AS `business_summary`,
  s.`updated_at` AS `sector_industry_updated_at`
FROM `stocks`.`symbol_sector_industry` s
LEFT JOIN `stocks`.`symbol_business_summary` b
  ON b.`symbol` = s.`symbol`;

