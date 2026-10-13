from apscheduler.schedulers.blocking import BlockingScheduler
from gamedaybot.espn.espn_bot import espn_bot
from gamedaybot.espn.env_vars import get_env_vars


def scheduler():
    """
    This function is used to schedule jobs to send messages.

    Parameters
    ----------
    None

    Returns
    -------
    None
    """
    data = get_env_vars()
    game_timezone = 'America/New_York'
    sched = BlockingScheduler(job_defaults={'misfire_grace_time': 15 * 60})
    ff_start_date = data['ff_start_date']
    ff_end_date = data['ff_end_date']
    my_timezone = data['my_timezone']

    # power rankings:                     tuesday evening at 6:30pm local time.
    # strength of schedule:               tuesday evening at 6:31pm local time.
    # clown watch:                        tuesday evening at 6:32pm local time.
    # fetti report (final scores, trophies): tuesday at 12:00pm local time.
    # luck report:                        tuesday at 12:05pm local time.
    # who's hot & who's not:             tuesday at 12:06pm local time.
    # standings:                          wednesday morning at 7:30am local time.
    # waiver report:                      wednesday morning at 7:31am local time. (optional)
    # matchups:                           thursday evening at 7:30pm east coast time.

    sched.add_job(espn_bot, 'cron', ['get_power_rankings'], id='power_rankings',
                  day_of_week='tue', hour=18, minute=30, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_final'], id='final',
                  day_of_week='tue', hour=12, minute=0, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_luck_report'], id='luck_report',
                  day_of_week='tue', hour=12, minute=5, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_form_report'], id='form_report',
                  day_of_week='tue', hour=12, minute=6, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_strength_of_schedule'], id='strength_of_schedule',
                  day_of_week='tue', hour=18, minute=31, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_clown_watch'], id='clown_watch',
                  day_of_week='tue', hour=18, minute=32, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_standings'], id='standings',
                  day_of_week='wed', hour=7, minute=30, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)
    sched.add_job(espn_bot, 'cron', ['get_waiver_report'], id='waiver_report',
                  day_of_week='wed', hour=7, minute=31, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=my_timezone, replace_existing=True)

    if data['daily_waiver']:
        sched.add_job(
            espn_bot, 'cron', ['get_waiver_report'],
            id='waiver_report', day_of_week='mon, tue, thu, fri, sat, sun', hour=7, minute=31, start_date=ff_start_date,
            end_date=ff_end_date, timezone=my_timezone, replace_existing=True)

    sched.add_job(espn_bot, 'cron', ['get_matchups'], id='matchups',
                  day_of_week='thu', hour=17, minute=30, start_date=ff_start_date, end_date=ff_end_date,
                  timezone=game_timezone, replace_existing=True)


    print("Ready!")
    sched.start()
