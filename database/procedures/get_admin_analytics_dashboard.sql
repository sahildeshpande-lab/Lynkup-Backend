DROP FUNCTION IF EXISTS get_admin_analytics_dashboard(INTEGER, TEXT);
DROP FUNCTION IF EXISTS get_admin_analytics_dashboard(INTEGER, TEXT, TEXT);


CREATE OR REPLACE FUNCTION get_admin_analytics_dashboard(
    p_days INTEGER DEFAULT 30,
    p_type TEXT DEFAULT NULL,
    p_timezone TEXT DEFAULT 'UTC'
)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_days INTEGER;
    v_type TEXT;
    v_timezone TEXT;
    v_end_date DATE;
    v_start_date DATE;

    v_total_users BIGINT := 0;
    v_total_posts BIGINT := 0;
    v_dau_today BIGINT := 0;
    v_new_regs_today BIGINT := 0;

    v_period JSONB;
    v_overview JSONB;
    v_registration_trend JSONB;
    v_dau_trend JSONB;
    v_distribution JSONB;
    v_learning_spotlight JSONB;

    v_dau_activities TEXT[] := ARRAY[
        'CREATE_POST',
        'CREATE_COMMENT',
        'LIKE_POST',
        'LIKE_COMMENT',
        'REPOST',
        'BOOKMARK_POST',
        'SHARE_POST',
        'SEND_CONNECTION_REQUEST',
        'ACCEPT_CONNECTION_REQUEST',
        'UPDATE_PROFILE'
    ];

BEGIN

    ----------------------------------------------------------------
    -- 1. Validate days
    ----------------------------------------------------------------
    IF p_days IS NULL OR p_days <= 0 THEN
        RAISE EXCEPTION 'days must be greater than 0';
    END IF;


    ----------------------------------------------------------------
    -- 2. Validate and normalize timezone
    ----------------------------------------------------------------
    v_timezone := COALESCE(
        NULLIF(btrim(p_timezone), ''),
        'UTC'
    );

    -- Validate timezone.
    PERFORM now() AT TIME ZONE v_timezone;


    ----------------------------------------------------------------
    -- 3. Calculate analytics period
    ----------------------------------------------------------------
    v_days := p_days;

    v_end_date :=
        (CURRENT_TIMESTAMP AT TIME ZONE v_timezone)::date;

    v_start_date :=
        v_end_date - (v_days - 1);


    ----------------------------------------------------------------
    -- 4. Validate distribution type
    ----------------------------------------------------------------
    IF p_type IS NULL OR btrim(p_type) = '' THEN
        v_type := NULL;
    ELSE
        v_type := lower(btrim(p_type));

        IF v_type NOT IN ('university', 'country', 'major') THEN
            RAISE EXCEPTION
                'type must be one of: university, country, major';
        END IF;
    END IF;


    ----------------------------------------------------------------
    -- 5. TOTAL USERS
    --
    -- Count only users registered within selected period.
    ----------------------------------------------------------------
    SELECT COUNT(*)
    INTO v_total_users
    FROM users u
    INNER JOIN user_roles ur
        ON ur.user_id = u.id
    INNER JOIN roles r
        ON r.id = ur.role_id
    WHERE COALESCE(u.is_deleted, FALSE) = FALSE
      AND r.name = 'user'
      AND (u.created_at AT TIME ZONE v_timezone)::date
          BETWEEN v_start_date AND v_end_date;


    ----------------------------------------------------------------
    -- 6. TOTAL POSTS
    --
    -- Count only posts created within selected period.
    -- Deleted posts are excluded.
    ----------------------------------------------------------------
    SELECT COUNT(*)
    INTO v_total_posts
    FROM posts p
    WHERE p.state::text <> 'deleted'
      AND (p.created_at AT TIME ZONE v_timezone)::date
          BETWEEN v_start_date AND v_end_date;


    ----------------------------------------------------------------
    -- 7. NEW REGISTRATIONS TODAY
    ----------------------------------------------------------------
    SELECT COUNT(*)
    INTO v_new_regs_today
    FROM users u
    INNER JOIN user_roles ur
        ON ur.user_id = u.id
    INNER JOIN roles r
        ON r.id = ur.role_id
    WHERE COALESCE(u.is_deleted, FALSE) = FALSE
      AND r.name = 'user'
      AND (u.created_at AT TIME ZONE v_timezone)::date = v_end_date;


    ----------------------------------------------------------------
    -- 8. DAU TODAY
    ----------------------------------------------------------------
    SELECT COUNT(DISTINCT ual.user_id)
    INTO v_dau_today
    FROM user_activity_logs ual
    WHERE (ual.created_at AT TIME ZONE v_timezone)::date = v_end_date
      AND ual.activity_log = ANY (v_dau_activities);


    ----------------------------------------------------------------
    -- 9. PERIOD
    ----------------------------------------------------------------
    v_period := jsonb_build_object(
        'days', v_days,
        'start_date', to_char(v_start_date, 'YYYY-MM-DD'),
        'end_date', to_char(v_end_date, 'YYYY-MM-DD')
    );


    ----------------------------------------------------------------
    -- 10. LEARNING SPOTLIGHT ANALYTICS
    --
    -- The cycle is dynamically read from:
    --
    -- learning_recommendation_settings.cycle_configuration->'cycle'
    --
    -- Example:
    --
    -- {
    --   "cycle": [
    --     "country_perspective",
    --     "leading_thinker",
    --     "influential_research",
    --     "latest_research",
    --     "beyond_your_field"
    --   ]
    -- }
    --
    -- Array position determines cycle_day:
    --
    -- 1 -> country_perspective
    -- 2 -> leading_thinker
    -- 3 -> influential_research
    -- 4 -> latest_research
    -- 5 -> beyond_your_field
    --
    -- The configured cycle is the source of truth for both:
    -- 1. Top 10 cycle labels
    -- 2. Summary read counts
    ----------------------------------------------------------------

    WITH spotlight_snapshots AS (

        ------------------------------------------------------------
        -- Current Spotlight
        ------------------------------------------------------------
        SELECT
            p.user_id,

            p.learning_spotlight->>'spotlight_type'
                AS spotlight_type,

            p.learning_spotlight->>'cycle_day'
                AS cycle_day,

            (p.learning_spotlight->>'generated_at')::timestamptz
                AS generated_at,

            CASE
                WHEN jsonb_typeof(
                    p.learning_spotlight->'papers'
                ) = 'array'
                AND jsonb_array_length(
                    p.learning_spotlight->'papers'
                ) > 0
                    THEN p.learning_spotlight->'papers'

                WHEN p.learning_spotlight->'paper' IS NOT NULL
                AND jsonb_typeof(
                    p.learning_spotlight->'paper'
                ) = 'object'
                    THEN jsonb_build_array(
                        p.learning_spotlight->'paper'
                    )

                ELSE '[]'::jsonb
            END AS papers,

            1 AS source_rank

        FROM profiles p

        WHERE p.learning_spotlight IS NOT NULL


        UNION ALL


        ------------------------------------------------------------
        -- Historical Spotlights
        ------------------------------------------------------------
        SELECT
            l.user_id,

            l.learning_recommendations->>'spotlight_type'
                AS spotlight_type,

            l.learning_recommendations->>'cycle_day'
                AS cycle_day,

            (
                l.learning_recommendations->>'generated_at'
            )::timestamptz AS generated_at,

            CASE
                WHEN jsonb_typeof(
                    l.learning_recommendations->'papers'
                ) = 'array'
                AND jsonb_array_length(
                    l.learning_recommendations->'papers'
                ) > 0
                    THEN l.learning_recommendations->'papers'

                WHEN l.learning_recommendations->'paper' IS NOT NULL
                AND jsonb_typeof(
                    l.learning_recommendations->'paper'
                ) = 'object'
                    THEN jsonb_build_array(
                        l.learning_recommendations->'paper'
                    )

                ELSE '[]'::jsonb
            END AS papers,

            2 AS source_rank

        FROM learning_recommendation_logs l

        WHERE l.learning_recommendations IS NOT NULL
    ),


    ----------------------------------------------------------------
    -- Deduplicate current + historical snapshots
    --
    -- Current and historical can contain the same snapshot.
    ----------------------------------------------------------------
    deduplicated_snapshots AS (

        SELECT DISTINCT ON (
            user_id,
            generated_at
        )
            user_id,
            spotlight_type,
            cycle_day,
            generated_at,
            papers

        FROM spotlight_snapshots

        WHERE generated_at IS NOT NULL

        ORDER BY
            user_id,
            generated_at,
            source_rank ASC
    ),


    ----------------------------------------------------------------
    -- Expand recommended papers
    ----------------------------------------------------------------
    recommended_papers AS (

        SELECT
            s.user_id,

            s.spotlight_type,

            NULLIF(
                s.cycle_day,
                ''
            )::INTEGER AS cycle_day,

            s.generated_at,

            paper->>'paper_id' AS paper_id,

            paper->>'title' AS article_name

        FROM deduplicated_snapshots s

        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(
                s.papers,
                '[]'::jsonb
            )
        ) AS paper
    ),


    ----------------------------------------------------------------
    -- Filter recommendations by analytics period
    ----------------------------------------------------------------
    filtered_recommendations AS (

        SELECT
            r.*

        FROM recommended_papers r

        WHERE (
            r.generated_at AT TIME ZONE v_timezone
        )::date BETWEEN v_start_date AND v_end_date
    ),


    ----------------------------------------------------------------
    -- Check whether Spotlight data exists
    ----------------------------------------------------------------
    has_spotlight_data AS (

        SELECT EXISTS (
            SELECT 1
            FROM filtered_recommendations
            LIMIT 1
        ) AS has_data
    ),


    ----------------------------------------------------------------
    -- Get cycle directly from settings
    --
    -- Array position = cycle_day.
    --
    -- This is the SINGLE SOURCE OF TRUTH for cycle mapping.
    ----------------------------------------------------------------
    cycle_configuration AS (

        SELECT
            c.spotlight_type,
            c.cycle_day

        FROM learning_recommendation_settings lrs

        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(
                lrs.cycle_configuration->'cycle',
                '[]'::jsonb
            )
        ) WITH ORDINALITY AS c(
            spotlight_type,
            cycle_day
        )
    ),


    ----------------------------------------------------------------
    -- Recommendation count per paper
    --
    -- IMPORTANT:
    -- Use cycle_configuration.spotlight_type instead of the
    -- spotlight_type stored in the historical/current snapshot.
    ----------------------------------------------------------------
    paper_recommendations AS (

        SELECT
            fr.cycle_day,

            cc.spotlight_type AS spotlight_type,

            fr.paper_id,

            MAX(fr.article_name) AS article_name,

            COUNT(DISTINCT fr.user_id)::BIGINT
                AS total_users_recommended

        FROM filtered_recommendations fr

        INNER JOIN cycle_configuration cc
            ON cc.cycle_day = fr.cycle_day

        WHERE fr.paper_id IS NOT NULL
          AND fr.cycle_day IS NOT NULL

        GROUP BY
            fr.cycle_day,
            cc.spotlight_type,
            fr.paper_id
    ),


    ----------------------------------------------------------------
    -- Total read time per paper
    --
    -- One recommendation row per:
    -- cycle_day + spotlight_type + paper_id + user_id
    --
    -- Every valid READ interaction contributes to the SUM.
    -- Therefore repeated READ interactions increase total time.
    ----------------------------------------------------------------
    paper_read_time AS (

        SELECT
            r.cycle_day,

            r.spotlight_type,

            r.paper_id,

            COALESCE(
                SUM(i.read_time_seconds),
                0
            )::BIGINT AS total_read_time_seconds

        FROM (
            SELECT DISTINCT
                fr.cycle_day,

                cc.spotlight_type AS spotlight_type,

                fr.paper_id,

                fr.user_id

            FROM filtered_recommendations fr

            INNER JOIN cycle_configuration cc
                ON cc.cycle_day = fr.cycle_day

            WHERE fr.paper_id IS NOT NULL
              AND fr.cycle_day IS NOT NULL
        ) r

        INNER JOIN learning_paper_interactions i
            ON i.user_id = r.user_id

            AND i.paper_id = r.paper_id

            AND i.action = 'READ'

            AND i.read_time_seconds IS NOT NULL

            AND i.read_time_seconds > 0

        GROUP BY
            r.cycle_day,
            r.spotlight_type,
            r.paper_id
    ),


    ----------------------------------------------------------------
    -- Combine recommendation count + read time
    ----------------------------------------------------------------
    combined AS (

        SELECT
            pr.cycle_day,

            pr.spotlight_type,

            pr.paper_id,

            pr.article_name,

            pr.total_users_recommended,

            COALESCE(
                rt.total_read_time_seconds,
                0
            ) AS total_read_time_seconds

        FROM paper_recommendations pr

        LEFT JOIN paper_read_time rt
            ON rt.cycle_day = pr.cycle_day

            AND rt.spotlight_type = pr.spotlight_type

            AND rt.paper_id = pr.paper_id
    ),


    ----------------------------------------------------------------
    -- Only papers with actual read time enter Top 10
    ----------------------------------------------------------------
    read_papers AS (

        SELECT *
        FROM combined

        WHERE total_read_time_seconds > 0
    ),


    ----------------------------------------------------------------
    -- Rank papers within each cycle
    --
    -- 1. Highest total read time
    -- 2. Highest users recommended
    -- 3. paper_id deterministic tie-breaker
    ----------------------------------------------------------------
    ranked AS (

        SELECT
            *,

            ROW_NUMBER() OVER (
                PARTITION BY cycle_day

                ORDER BY
                    total_read_time_seconds DESC,
                    total_users_recommended DESC,
                    paper_id ASC
            ) AS rank

        FROM read_papers
    ),


    ----------------------------------------------------------------
    -- Top 10 papers for every cycle day
    ----------------------------------------------------------------
    top_10 AS (

        SELECT
            cycle_day,
            spotlight_type,
            rank,
            paper_id,
            article_name,
            total_users_recommended,
            total_read_time_seconds

        FROM ranked

        WHERE rank <= 10
    ),


    ----------------------------------------------------------------
    -- Aggregate Top 10 papers
    ----------------------------------------------------------------
    top_10_by_cycle AS (

        SELECT
            cycle_day,

            jsonb_agg(
                jsonb_build_object(
                    'rank',
                    rank,

                    'paper_id',
                    paper_id,

                    'article_name',
                    article_name,

                    'total_users_recommended',
                    total_users_recommended,

                    'total_read_time_seconds',
                    total_read_time_seconds
                )
                ORDER BY rank
            ) AS papers

        FROM top_10

        GROUP BY cycle_day
    ),


    ----------------------------------------------------------------
    -- READ SUMMARY
    --
    -- Analytics definition:
    --
    --   1. Multiple READ interactions for the same user + paper
    --      count as ONE read.
    --
    --   2. A user + paper must belong to EXACTLY ONE Spotlight type
    --      in the summary.
    --
    --   3. If the same user + paper exists under multiple Spotlight
    --      recommendations, the most recent recommendation in the
    --      selected analytics period determines the type.
    --
    -- This guarantees:
    --
    --   leading_thinker
    -- + country_perspective
    -- + influential_research
    -- + latest_research
    -- + beyond_your_field
    -- = total_read
    ----------------------------------------------------------------
    type_reads AS (

        SELECT DISTINCT ON (
            fr.user_id,
            fr.paper_id
        )
            fr.user_id,
            fr.paper_id,
            cc.spotlight_type

        FROM filtered_recommendations fr

        INNER JOIN cycle_configuration cc
            ON cc.cycle_day = fr.cycle_day

        INNER JOIN learning_paper_interactions i
            ON i.user_id = fr.user_id
            AND i.paper_id = fr.paper_id
            AND i.action = 'READ'
            AND (
                i.created_at AT TIME ZONE v_timezone
            )::date BETWEEN v_start_date AND v_end_date

        WHERE fr.paper_id IS NOT NULL
          AND fr.cycle_day IS NOT NULL

        ORDER BY
            fr.user_id,
            fr.paper_id,
            fr.generated_at DESC,
            fr.cycle_day DESC
    ),


    ----------------------------------------------------------------
    -- Summary by Spotlight type
    --
    -- type_reads has exactly one row per unique:
    --     user + paper
    --
    -- Therefore each read is assigned to exactly one type.
    ----------------------------------------------------------------
    summary AS (

        SELECT

            COUNT(*) FILTER (
                WHERE spotlight_type = 'leading_thinker'
            )::BIGINT AS leading_thinker_read,

            COUNT(*) FILTER (
                WHERE spotlight_type = 'country_perspective'
            )::BIGINT AS country_perspective_read,

            COUNT(*) FILTER (
                WHERE spotlight_type = 'influential_research'
            )::BIGINT AS influential_research_read,

            COUNT(*) FILTER (
                WHERE spotlight_type = 'latest_research'
            )::BIGINT AS latest_research_read,

            COUNT(*) FILTER (
                WHERE spotlight_type = 'beyond_your_field'
            )::BIGINT AS beyond_your_field_read

        FROM type_reads
    ),


    ----------------------------------------------------------------
    -- Overall READ count
    --
    -- type_reads contains exactly one row per unique:
    --     user + paper
    --
    -- Therefore this MUST equal the sum of all five type counts.
    ----------------------------------------------------------------
    total_reads AS (

        SELECT
            COUNT(*)::BIGINT AS total_read
        FROM type_reads
    )


    ----------------------------------------------------------------
    -- Build Learning Spotlight response
    ----------------------------------------------------------------
    SELECT jsonb_build_object(

        ------------------------------------------------------------
        -- Summary
        ------------------------------------------------------------
        'summary',

        jsonb_build_object(

            'leading_thinker',

            jsonb_build_object(
                'total_read',
                COALESCE(
                    s.leading_thinker_read,
                    0
                )
            ),


            'country_perspective',

            jsonb_build_object(
                'total_read',
                COALESCE(
                    s.country_perspective_read,
                    0
                )
            ),


            'influential_research',

            jsonb_build_object(
                'total_read',
                COALESCE(
                    s.influential_research_read,
                    0
                )
            ),


            'latest_research',

            jsonb_build_object(
                'total_read',
                COALESCE(
                    s.latest_research_read,
                    0
                )
            ),


            'beyond_your_field',

            jsonb_build_object(
                'total_read',
                COALESCE(
                    s.beyond_your_field_read,
                    0
                )
            ),


            'total_read',

            COALESCE(
                tr.total_read,
                0
            )
        ),


        ------------------------------------------------------------
        -- Top 10 papers grouped by cycle
        --
        -- Cycle labels come directly from:
        -- learning_recommendation_settings.cycle_configuration
        ------------------------------------------------------------
        'cycles',

        CASE

            WHEN NOT (
                SELECT has_data
                FROM has_spotlight_data
            )

                THEN '[]'::jsonb


            ELSE COALESCE(

                (
                    SELECT jsonb_agg(

                        jsonb_build_object(

                            'cycle_day',
                            cc.cycle_day,


                            'spotlight_type',
                            cc.spotlight_type,


                            'papers',
                            COALESCE(
                                p.papers,
                                '[]'::jsonb
                            )

                        )

                        ORDER BY cc.cycle_day
                    )

                    FROM cycle_configuration cc

                    LEFT JOIN top_10_by_cycle p
                        ON p.cycle_day = cc.cycle_day
                ),

                '[]'::jsonb
            )

        END

    )

    INTO v_learning_spotlight

    FROM summary s

    CROSS JOIN total_reads tr;


    ----------------------------------------------------------------
    -- Fallback
    ----------------------------------------------------------------
    IF v_learning_spotlight IS NULL THEN

        v_learning_spotlight := jsonb_build_object(

            'summary',

            jsonb_build_object(

                'leading_thinker',

                jsonb_build_object(
                    'total_read', 0
                ),


                'country_perspective',

                jsonb_build_object(
                    'total_read', 0
                ),


                'influential_research',

                jsonb_build_object(
                    'total_read', 0
                ),


                'latest_research',

                jsonb_build_object(
                    'total_read', 0
                ),


                'beyond_your_field',

                jsonb_build_object(
                    'total_read', 0
                ),


                'total_read',
                0
            ),


            'cycles',
            '[]'::jsonb
        );

    END IF;


    ----------------------------------------------------------------
    -- 11. OVERVIEW
    ----------------------------------------------------------------
    v_overview := jsonb_build_object(

        'total_users',
        v_total_users,

        'daily_active_users',
        v_dau_today,

        'new_registrations_today',
        v_new_regs_today,

        'total_posts',
        v_total_posts,

        'learning_spotlight',
        v_learning_spotlight
    );


    ----------------------------------------------------------------
    -- 12. USER REGISTRATION TREND
    ----------------------------------------------------------------
    SELECT COALESCE(

        jsonb_agg(

            jsonb_build_object(

                'date',
                to_char(
                    d.day,
                    'YYYY-MM-DD'
                ),

                'count',
                COALESCE(
                    reg.cnt,
                    0
                )
            )

            ORDER BY d.day
        ),

        '[]'::jsonb

    )

    INTO v_registration_trend

    FROM generate_series(

        v_start_date,

        v_end_date,

        INTERVAL '1 day'

    ) AS d(day)


    LEFT JOIN (

        SELECT

            (
                u.created_at
                AT TIME ZONE v_timezone
            )::date AS day,

            COUNT(*)::BIGINT AS cnt

        FROM users u

        INNER JOIN user_roles ur
            ON ur.user_id = u.id

        INNER JOIN roles r
            ON r.id = ur.role_id

        WHERE COALESCE(
            u.is_deleted,
            FALSE
        ) = FALSE

          AND r.name = 'user'

          AND (
              u.created_at
              AT TIME ZONE v_timezone
          )::date
              BETWEEN v_start_date AND v_end_date

        GROUP BY 1

    ) reg

        ON reg.day = d.day::date;


    ----------------------------------------------------------------
    -- 13. DAU TREND
    ----------------------------------------------------------------
    SELECT COALESCE(

        jsonb_agg(

            jsonb_build_object(

                'date',
                to_char(
                    d.day,
                    'YYYY-MM-DD'
                ),

                'count',
                COALESCE(
                    dau.cnt,
                    0
                )
            )

            ORDER BY d.day
        ),

        '[]'::jsonb

    )

    INTO v_dau_trend

    FROM generate_series(

        v_start_date,

        v_end_date,

        INTERVAL '1 day'

    ) AS d(day)


    LEFT JOIN (

        SELECT

            (
                ual.created_at
                AT TIME ZONE v_timezone
            )::date AS day,

            COUNT(
                DISTINCT ual.user_id
            )::BIGINT AS cnt

        FROM user_activity_logs ual

        WHERE (
            ual.created_at
            AT TIME ZONE v_timezone
        )::date
            BETWEEN v_start_date AND v_end_date

          AND ual.activity_log = ANY(
              v_dau_activities
          )

        GROUP BY 1

    ) dau

        ON dau.day = d.day::date;


    ----------------------------------------------------------------
    -- 14. DISTRIBUTIONS
    --
    -- All distributions are based ONLY on users registered
    -- within selected analytics period.
    ----------------------------------------------------------------
    IF v_type IS NULL THEN

        v_distribution := jsonb_build_object(

            --------------------------------------------------------
            -- UNIVERSITIES
            --------------------------------------------------------
            'universities',

            (

                SELECT COALESCE(

                    jsonb_agg(

                        jsonb_build_object(

                            'name',
                            x.name,

                            'iso_code',
                            x.iso_code,

                            'count',
                            x.cnt,

                            'percentage',

                            CASE

                                WHEN v_total_users = 0
                                    THEN 0

                                ELSE round(

                                    (
                                        x.cnt::numeric
                                        * 100.0
                                    )
                                    / v_total_users,

                                    1
                                )

                            END
                        )

                        ORDER BY
                            x.cnt DESC,
                            x.name ASC
                    ),

                    '[]'::jsonb
                )

                FROM (

                    SELECT

                        un.name AS name,

                        NULL::TEXT AS iso_code,

                        COUNT(*)::BIGINT AS cnt

                    FROM profiles pr

                    INNER JOIN users u
                        ON u.id = pr.user_id

                    INNER JOIN user_roles ur
                        ON ur.user_id = u.id

                    INNER JOIN roles r
                        ON r.id = ur.role_id

                    INNER JOIN universities un
                        ON un.id = pr.university_id

                    WHERE COALESCE(
                        u.is_deleted,
                        FALSE
                    ) = FALSE

                      AND r.name = 'user'

                      AND pr.university_id IS NOT NULL

                      AND (
                          u.created_at
                          AT TIME ZONE v_timezone
                      )::date
                          BETWEEN v_start_date
                          AND v_end_date

                    GROUP BY un.name

                    ORDER BY
                        COUNT(*) DESC,
                        un.name ASC

                    LIMIT 5

                ) x
            ),


            --------------------------------------------------------
            -- COUNTRIES
            --------------------------------------------------------
            'countries',

            (

                SELECT COALESCE(

                    jsonb_agg(

                        jsonb_build_object(

                            'name',
                            x.name,

                            'iso_code',
                            x.iso_code,

                            'count',
                            x.cnt,

                            'percentage',

                            CASE

                                WHEN v_total_users = 0
                                    THEN 0

                                ELSE round(

                                    (
                                        x.cnt::numeric
                                        * 100.0
                                    )
                                    / v_total_users,

                                    1
                                )

                            END
                        )

                        ORDER BY
                            x.cnt DESC,
                            x.name ASC
                    ),

                    '[]'::jsonb
                )

                FROM (

                    SELECT

                        c.name AS name,

                        c.iso_code AS iso_code,

                        COUNT(*)::BIGINT AS cnt

                    FROM profiles pr

                    INNER JOIN users u
                        ON u.id = pr.user_id

                    INNER JOIN user_roles ur
                        ON ur.user_id = u.id

                    INNER JOIN roles r
                        ON r.id = ur.role_id

                    INNER JOIN countries c
                        ON c.id = pr.country_id

                    WHERE COALESCE(
                        u.is_deleted,
                        FALSE
                    ) = FALSE

                      AND r.name = 'user'

                      AND pr.country_id IS NOT NULL

                      AND (
                          u.created_at
                          AT TIME ZONE v_timezone
                      )::date
                          BETWEEN v_start_date
                          AND v_end_date

                    GROUP BY
                        c.name,
                        c.iso_code

                    ORDER BY
                        COUNT(*) DESC,
                        c.name ASC

                    LIMIT 5

                ) x
            ),


            --------------------------------------------------------
            -- MAJORS
            --------------------------------------------------------
            'majors',

            (

                SELECT COALESCE(

                    jsonb_agg(

                        jsonb_build_object(

                            'name',
                            x.name,

                            'iso_code',
                            x.iso_code,

                            'count',
                            x.cnt,

                            'percentage',

                            CASE

                                WHEN v_total_users = 0
                                    THEN 0

                                ELSE round(

                                    (
                                        x.cnt::numeric
                                        * 100.0
                                    )
                                    / v_total_users,

                                    1
                                )

                            END
                        )

                        ORDER BY
                            x.cnt DESC,
                            x.name ASC
                    ),

                    '[]'::jsonb
                )

                FROM (

                    SELECT

                        btrim(
                            pr.major
                        ) AS name,

                        NULL::TEXT AS iso_code,

                        COUNT(*)::BIGINT AS cnt

                    FROM profiles pr

                    INNER JOIN users u
                        ON u.id = pr.user_id

                    INNER JOIN user_roles ur
                        ON ur.user_id = u.id

                    INNER JOIN roles r
                        ON r.id = ur.role_id

                    WHERE COALESCE(
                        u.is_deleted,
                        FALSE
                    ) = FALSE

                      AND r.name = 'user'

                      AND pr.major IS NOT NULL

                      AND btrim(
                          pr.major
                      ) <> ''

                      AND (
                          u.created_at
                          AT TIME ZONE v_timezone
                      )::date
                          BETWEEN v_start_date
                          AND v_end_date

                    GROUP BY btrim(
                        pr.major
                    )

                    ORDER BY
                        COUNT(*) DESC,
                        btrim(pr.major) ASC

                    LIMIT 5

                ) x
            )
        );


    ----------------------------------------------------------------
    -- 15. UNIVERSITY FILTER
    ----------------------------------------------------------------
    ELSIF v_type = 'university' THEN

        v_distribution := jsonb_build_object(

            'type',
            'university',

            'items',

            (

                SELECT COALESCE(

                    jsonb_agg(

                        jsonb_build_object(

                            'name',
                            x.name,

                            'count',
                            x.cnt,

                            'percentage',

                            CASE

                                WHEN v_total_users = 0
                                    THEN 0

                                ELSE round(

                                    (
                                        x.cnt::numeric
                                        * 100.0
                                    )
                                    / v_total_users,

                                    1
                                )

                            END
                        )

                        ORDER BY
                            x.cnt DESC,
                            x.name ASC
                    ),

                    '[]'::jsonb
                )

                FROM (

                    SELECT

                        un.name AS name,

                        COUNT(*)::BIGINT AS cnt

                    FROM profiles pr

                    INNER JOIN users u
                        ON u.id = pr.user_id

                    INNER JOIN user_roles ur
                        ON ur.user_id = u.id

                    INNER JOIN roles r
                        ON r.id = ur.role_id

                    INNER JOIN universities un
                        ON un.id = pr.university_id

                    WHERE COALESCE(
                        u.is_deleted,
                        FALSE
                    ) = FALSE

                      AND r.name = 'user'

                      AND pr.university_id IS NOT NULL

                      AND (
                          u.created_at
                          AT TIME ZONE v_timezone
                      )::date
                          BETWEEN v_start_date
                          AND v_end_date

                    GROUP BY un.name

                ) x
            )
        );


    ----------------------------------------------------------------
    -- 16. COUNTRY FILTER
    ----------------------------------------------------------------
    ELSIF v_type = 'country' THEN

        v_distribution := jsonb_build_object(

            'type',
            'country',

            'items',

            (

                SELECT COALESCE(

                    jsonb_agg(

                        jsonb_build_object(

                            'name',
                            x.name,

                            'iso_code',
                            x.iso_code,

                            'count',
                            x.cnt,

                            'percentage',

                            CASE

                                WHEN v_total_users = 0
                                    THEN 0

                                ELSE round(

                                    (
                                        x.cnt::numeric
                                        * 100.0
                                    )
                                    / v_total_users,

                                    1
                                )

                            END
                        )

                        ORDER BY
                            x.cnt DESC,
                            x.name ASC
                    ),

                    '[]'::jsonb
                )

                FROM (

                    SELECT

                        c.name AS name,

                        c.iso_code AS iso_code,

                        COUNT(*)::BIGINT AS cnt

                    FROM profiles pr

                    INNER JOIN users u
                        ON u.id = pr.user_id

                    INNER JOIN user_roles ur
                        ON ur.user_id = u.id

                    INNER JOIN roles r
                        ON r.id = ur.role_id

                    INNER JOIN countries c
                        ON c.id = pr.country_id

                    WHERE COALESCE(
                        u.is_deleted,
                        FALSE
                    ) = FALSE

                      AND r.name = 'user'

                      AND pr.country_id IS NOT NULL

                      AND (
                          u.created_at
                          AT TIME ZONE v_timezone
                      )::date
                          BETWEEN v_start_date
                          AND v_end_date

                    GROUP BY
                        c.name,
                        c.iso_code

                ) x
            )
        );


    ----------------------------------------------------------------
    -- 17. MAJOR FILTER
    ----------------------------------------------------------------
    ELSE

        v_distribution := jsonb_build_object(

            'type',
            'major',

            'items',

            (

                SELECT COALESCE(

                    jsonb_agg(

                        jsonb_build_object(

                            'name',
                            x.name,

                            'count',
                            x.cnt,

                            'percentage',

                            CASE

                                WHEN v_total_users = 0
                                    THEN 0

                                ELSE round(

                                    (
                                        x.cnt::numeric
                                        * 100.0
                                    )
                                    / v_total_users,

                                    1
                                )

                            END
                        )

                        ORDER BY
                            x.cnt DESC,
                            x.name ASC
                    ),

                    '[]'::jsonb
                )

                FROM (

                    SELECT

                        btrim(
                            pr.major
                        ) AS name,

                        COUNT(*)::BIGINT AS cnt

                    FROM profiles pr

                    INNER JOIN users u
                        ON u.id = pr.user_id

                    INNER JOIN user_roles ur
                        ON ur.user_id = u.id

                    INNER JOIN roles r
                        ON r.id = ur.role_id

                    WHERE COALESCE(
                        u.is_deleted,
                        FALSE
                    ) = FALSE

                      AND r.name = 'user'

                      AND pr.major IS NOT NULL

                      AND btrim(
                          pr.major
                      ) <> ''

                      AND (
                          u.created_at
                          AT TIME ZONE v_timezone
                      )::date
                          BETWEEN v_start_date
                          AND v_end_date

                    GROUP BY btrim(
                        pr.major
                    )

                ) x
            )
        );

    END IF;


    ----------------------------------------------------------------
    -- 18. FINAL RESPONSE
    ----------------------------------------------------------------
    RETURN jsonb_build_object(

        'period',
        v_period,

        'overview',
        v_overview,

        'registration_trend',
        v_registration_trend,

        'dau_trend',
        v_dau_trend,

        'distribution',
        v_distribution
    );

END;
$$;
