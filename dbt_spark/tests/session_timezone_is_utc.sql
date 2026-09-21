-- defensive: this test session must be UTC, otherwise every other time test below is being judged in the wrong zone
select current_timezone() as session_zone where current_timezone() not in ('Etc/UTC', 'UTC')
