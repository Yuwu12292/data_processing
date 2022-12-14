#!/usr/bin/python -t
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
# Copyright 2005 Duke University 

"""
Entrance point for the yum command line interface.
"""

import os
import os.path
import sys
import logging
import time
import unicodedata

from yum import Errors
from yum import plugins
from yum import logginglevels
from yum import _
from yum.i18n import to_unicode, utf8_width
import yum.misc
import cli
from utils import suppress_keyboard_interrupt_message, show_lock_owner

def main(args):
    """This does all the real work"""

    yum.misc.setup_locale(override_time=True)

    def exUserCancel():
        logger.critical(_('\n\nExiting on user cancel'))
        if unlock(): return 200
        return 1

    def exIOError(e):
        if e.errno == 32:
            logger.critical(_('\n\nExiting on Broken Pipe'))
        else:
            logger.critical(_('\n\n%s') % str(e))
        if unlock(): return 200
        return 1

    def exPluginExit(e):
        '''Called when a plugin raises PluginYumExit.

        Log the plugin's exit message if one was supplied.
        ''' # ' xemacs hack
        exitmsg = str(e)
        if exitmsg:
            logger.warn('\n\n%s', exitmsg)
        if unlock(): return 200
        return 1

    def exFatal(e):
        logger.critical('\n\n%s', to_unicode(e.value))
        if unlock(): return 200
        return 1

    def unlock():
        try:
            base.closeRpmDB()
            base.doUnlock()
        except Errors.LockError as e:
            return 200
        return 0


    logger = logging.getLogger("yum.main")
    verbose_logger = logging.getLogger("yum.verbose.main")

    # our core object for the cli
    base = cli.YumBaseCli()

    # do our cli parsing and config file setup
    # also sanity check the things being passed on the cli
    try:
        base.getOptionsConfig(args)
    except plugins.PluginYumExit as e:
        return exPluginExit(e)
    except Errors.YumBaseError as e:
        return exFatal(e)

    lockerr = ""
    while True:
        try:
            base.doLock()
        except Errors.LockError as e:
            if "%s" %(e.msg,) != lockerr:
                lockerr = "%s" %(e.msg,)
                logger.critical(lockerr)
            if not base.conf.exit_on_lock:
                logger.critical(_("Another app is currently holding the yum lock; waiting for it to exit..."))
                show_lock_owner(e.pid, logger)
                time.sleep(2)
            else:
                logger.critical(_("Another app is currently holding the yum lock; exiting as configured by exit_on_lock"))
                return 1
        else:
            break

    try:
        result, resultmsgs = base.doCommands()
    except plugins.PluginYumExit as e:
        return exPluginExit(e)
    except Errors.YumBaseError as e:
        result = 1
        # resultmsgs = [unicode(e)]
        resultmsgs = [unicodedata(e)]
    except KeyboardInterrupt:
        return exUserCancel()
    except IOError as e:
        return exIOError(e)

    # Act on the command/shell result
    if result == 0:
        # Normal exit 
        for msg in resultmsgs:
            verbose_logger.log(logginglevels.INFO_2, '%s', msg)
        if unlock(): return 200
        return 0
    elif result == 1:
        # Fatal error
        for msg in resultmsgs:
            logger.critical(_('Error: %s'), msg)
        if unlock(): return 200
        return 1
    elif result == 2:
        # Continue on
        pass
    elif result == 100:
        if unlock(): return 200
        return 100
    else:
        logger.critical(_('Unknown Error(s): Exit Code: %d:'), result)
        for msg in resultmsgs:
            logger.critical(msg)
        if unlock(): return 200
        return 3
            
    # Depsolve stage
    verbose_logger.log(logginglevels.INFO_2, _('Resolving Dependencies'))

    try:
        (result, resultmsgs) = base.buildTransaction() 
    except plugins.PluginYumExit as e:
        return exPluginExit(e)
    except Errors.YumBaseError as e:
        result = 1
        resultmsgs = [unicodedata(e)]
        # resultmsgs = [unicode(e)]#???????????????
    except KeyboardInterrupt:
        return exUserCancel()
    except IOError as e:
        return exIOError(e)
   
    # Act on the depsolve result
    if result == 0:
        # Normal exit
        if unlock(): return 200
        return 0
    elif result == 1:
        # Fatal error
        for msg in resultmsgs:
            prefix = _('Error: %s')
            prefix2nd = (' ' * (utf8_width(prefix) - 2))
            logger.critical(prefix, msg.replace('\n', '\n' + prefix2nd))
        if not base.conf.skip_broken:
            verbose_logger.info(_(" You could try using --skip-broken to work around the problem"))
        if not base._rpmdb_warn_checks(out=verbose_logger.info, warn=False):
            verbose_logger.info(_(" You could try running: rpm -Va --nofiles --nodigest"))
        if unlock(): return 200
        return 1
    elif result == 2:
        # Continue on
        pass
    else:
        logger.critical(_('Unknown Error(s): Exit Code: %d:'), result)
        for msg in resultmsgs:
            logger.critical(msg)
        if unlock(): return 200
        return 3

    verbose_logger.log(logginglevels.INFO_2, _('\nDependencies Resolved'))

    # Run the transaction
    try:
        return_code = base.doTransaction()
    except plugins.PluginYumExit as e:
        return exPluginExit(e)
    except Errors.YumBaseError as e:
        return exFatal(e)
    except KeyboardInterrupt:
        return exUserCancel()
    except IOError as e:
        return exIOError(e)

    # rpm_check_debug failed.
    if type(return_code) == type((0,)) and len(return_code) == 2:
        (result, resultmsgs) = return_code
        for msg in resultmsgs:
            logger.critical("%s", msg)
        if not base._rpmdb_warn_checks(out=verbose_logger.info, warn=False):
            verbose_logger.info(_(" You could try running: rpm -Va --nofiles --nodigest"))
        return_code = result
    else:
        verbose_logger.log(logginglevels.INFO_2, _('Complete!'))

    if unlock(): return 200
    return return_code

def hotshot(func, *args, **kwargs):
    # import hotshot#?????????????????????,??????
    import cProfile
    # import hotshot as hotshot
    fn = os.path.expanduser("~/yum.prof")
    prof = cProfile.Profile(fn)
    rc = prof.runcall(func, *args, **kwargs)
    prof.close()
    print_stats(hotshot.stats.load(fn))
    return rc

def cprof(func, *args, **kwargs):
    import cProfile, pstats
    fn = os.path.expanduser("~/yum.prof")
    prof = cProfile.Profile()
    rc = prof.runcall(func, *args, **kwargs)
    prof.dump_stats(fn)
    print_stats(pstats.Stats(fn))
    return rc

def print_stats(stats):
    stats.strip_dirs()
    stats.sort_stats('time', 'calls')
    stats.print_stats(20)
    stats.sort_stats('cumulative')
    stats.print_stats(40)

def user_main(args, exit_code=False):
    """ This calls one of the multiple main() functions based on env. vars """
    errcode = None
    if 'YUM_PROF' in os.environ:
        if os.environ['YUM_PROF'] == 'cprof':
            errcode = cprof(main, args)
        if os.environ['YUM_PROF'] == 'hotshot':
            errcode = hotshot(main, args)
    if 'YUM_PDB' in os.environ:
        import pdb
        pdb.run(main(args))

    if errcode is None:
        errcode = main(args)
    if exit_code:
        sys.exit(errcode)
    return errcode

suppress_keyboard_interrupt_message()

if __name__ == "__main__":
    try:
        user_main(sys.argv[1:], exit_code=True)
    except KeyboardInterrupt as e:
        print >> sys.stderr, _("\n\nExiting on user cancel.")
        sys.exit(1)
