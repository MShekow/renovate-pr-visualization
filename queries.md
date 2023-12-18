```sql
WITH weeks AS (
    SELECT generate_series(
                   DATE_TRUNC('week', MIN(created_date)),
                   DATE_TRUNC('week', CURRENT_DATE),
                   '1 week'::interval
               ) AS week
    FROM pull_request
),
     dep_update_weeks AS (
         SELECT weeks.week, d.updt AS update_type
         FROM weeks
                  JOIN (
             SELECT du.update_type as updt, du.*, pr.created_date, pr.closed_date
             FROM dependency_update du
                      LEFT JOIN pull_request pr ON du.pr_id = pr.id
         ) d
                       ON weeks.week >= DATE_TRUNC('week', d.created_date)
         WHERE d.closed_date IS NULL OR weeks.week <= DATE_TRUNC('week', d.closed_date)
     )
SELECT
    week AS "week start",
    update_type,
    COUNT(*) AS open_tickets
FROM dep_update_weeks
GROUP BY week, update_type
ORDER BY week, update_type;
```

Updated to include metabase field variables:
```sql
WITH weeks AS (
    SELECT generate_series(
                   DATE_TRUNC(
                     'week', [[ {{start_date}} --]]MIN(created_date)
                   ),
                   DATE_TRUNC(
                     'week', [[ {{end_date}} --]]CURRENT_DATE
                   ),
                   '1 week'::interval
               ) AS week
    FROM pull_request
),
     dep_update_weeks AS (
         SELECT weeks.week, d.updt AS update_type
         FROM weeks
                  JOIN (
             SELECT du.update_type as updt, pr.repo, pr.created_date, pr.closed_date
             FROM dependency_update du
                      LEFT JOIN pull_request pr ON du.pr_id = pr.id
         ) d
                       ON weeks.week >= DATE_TRUNC('week', d.created_date)
         WHERE (d.closed_date IS NULL OR weeks.week <= DATE_TRUNC('week', d.closed_date)) [[AND d.repo = {{repo}}]]
     )
SELECT
    week AS "week start",
    update_type,
    COUNT(*) AS open_tickets
FROM dep_update_weeks
GROUP BY week, update_type
ORDER BY week, update_type;
```
