#!/usr/bin/python -tt
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
The Yum RPM software updater.
"""

from filecmp import cmp
import os
import os.path
from pickle import LONG
import rpm
import re
import types
import errno
import time
import glob
import fnmatch
import logging
import logging.config
import operator


import yum.i18n
_ = yum.i18n._
P_ = yum.i18n.P_

import config
from config import ParsingError, ConfigParser
import Errors
import rpmsack
import rpmUtils.updates
from rpmUtils.arch import archDifference, canCoinstall, ArchStorage, isMultiLibArch
from rpmUtils.miscutils import compareEVR
import rpmUtils.transaction
import comps
import pkgtag_db
from repos import RepoStorage
import misc
from parser import ConfigPreProcessor, varReplace
import transactioninfo
import urlgrabber
from urlgrabber.grabber import URLGrabber, URLGrabError
from urlgrabber.progress import format_number
from packageSack import packagesNewestByName, packagesNewestByNameArch, ListPackageSack
import depsolve
import plugins
import logginglevels
import yumRepo
import callbacks
import yum.history

import warnings
warnings.simplefilter("ignore", Errors.YumFutureDeprecationWarning)

from packages import parsePackages, comparePoEVR
from packages import YumAvailablePackage, YumLocalPackage, YumInstalledPackage
from packages import YumUrlPackage
from constants import *
from yum.rpmtrans import RPMTransaction,SimpleCliCallBack
from yum.i18n import to_unicode, to_str

import string

from weakref import proxy as weakref

from urlgrabber.grabber import default_grabber

__version__ = '3.2.28'
__version_info__ = tuple([ int(num) for num in __version__.split('.')])

#  Setup a default_grabber UA here that says we are yum, done using the global
# so that other API users can easily add to it if they want.
#  Don't do it at init time, or we'll get multiple additions if you create
# multiple YumBase() objects.
default_grabber.opts.user_agent += " yum/" + __version__

class _YumPreBaseConf:
    """This is the configuration interface for the YumBase configuration.
       So if you want to change if plugins are on/off, or debuglevel/etc.
       you tweak it here, and when yb.conf does it's thing ... it happens. """

    def __init__(self):
        self.fn = '/etc/yum/yum.conf'
        self.root = '/'
        self.init_plugins = True
        self.plugin_types = (plugins.TYPE_CORE,)
        self.optparser = None
        self.debuglevel = None
        self.errorlevel = None
        self.disabled_plugins = None
        self.enabled_plugins = None
        self.syslog_ident = None
        self.syslog_facility = None
        self.syslog_device = None
        self.arch = None
        self.releasever = None
        self.uuid = None


class _YumPreRepoConf:
    """This is the configuration interface for the repos configuration.
       So if you want to change callbacks etc. you tweak it here, and when
       yb.repos does it's thing ... it happens. """

    def __init__(self):
        self.progressbar = None
        self.callback = None
        self.failure_callback = None
        self.interrupt_callback = None
        self.confirm_func = None
        self.gpg_import_func = None
        self.cachedir = None
        self.cache = None


class _YumCostExclude:
    """ This excludes packages that are in repos. of lower cost than the passed
        repo. """

    def __init__(self, repo, repos):
        self.repo   = weakref(repo)
        self._repos = weakref(repos)

    def __contains__(self, pkgtup):
        # (n, a, e, v, r) = pkgtup
        for repo in self._repos.listEnabled():
            if repo.cost >= self.repo.cost:
                break
            #  searchNevra is a bit slower, although more generic for repos. 
            # that don't use sqlitesack as the backend ... although they are
            # probably screwed anyway.
            #
            # if repo.sack.searchNevra(n, e, v, r, a):
            if pkgtup in repo.sack._pkgtup2pkgs:
                return True
        return False

class YumBase(depsolve.Depsolve):
    """This is a primary structure and base class. It houses the objects and
       methods needed to perform most things in yum. It is almost an abstract
       class in that you will need to add your own class above it for most
       real use."""
    
    def __init__(self):
        depsolve.Depsolve.__init__(self)
        self._conf = None
        self._tsInfo = None
        self._rpmdb = None
        self._up = None
        self._comps = None
        self._history = None
        self._pkgSack = None
        self._lockfile = None
        self._tags = None
        self.skipped_packages = []   # packages skip by the skip-broken code
        self.logger = logging.getLogger("yum.YumBase")
        self.verbose_logger = logging.getLogger("yum.verbose.YumBase")
        self._repos = RepoStorage(self)
        self.repo_setopts = {} # since we have to use repo_setopts in base and 
                               # not in cli - set it up as empty so no one
                               # trips over it later

        # Start with plugins disabled
        self.disablePlugins()

        self.localPackages = [] # for local package handling

        self.mediagrabber = None
        self.arch = ArchStorage()
        self.preconf = _YumPreBaseConf()
        self.prerepoconf = _YumPreRepoConf()

        self.run_with_package_names = set()

    def __del__(self):
        self.close()
        self.closeRpmDB()
        self.doUnlock()

    def close(self):
        # We don't want to create the object, so we test if it's been created
        if self._history is not None:
            self.history.close()

        if self._repos:
            self._repos.close()

    def _transactionDataFactory(self):
        """Factory method returning TransactionData object"""
        return transactioninfo.TransactionData()

    def doGenericSetup(self, cache=0):
        """do a default setup for all the normal/necessary yum components,
           really just a shorthand for testing"""

        self.preconf.init_plugins = False
        self.conf.cache = cache

    def doConfigSetup(self, fn='/etc/yum/yum.conf', root='/', init_plugins=True,
            plugin_types=(plugins.TYPE_CORE,), optparser=None, debuglevel=None,
            errorlevel=None):
        warnings.warn(_('doConfigSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        if hasattr(self, 'preconf'):
            self.preconf.fn = fn
            self.preconf.root = root
            self.preconf.init_plugins = init_plugins
            self.preconf.plugin_types = plugin_types
            self.preconf.optparser = optparser
            self.preconf.debuglevel = debuglevel
            self.preconf.errorlevel = errorlevel

        return self.conf
        
    def _getConfig(self, **kwargs):
        '''
        Parse and load Yum's configuration files and call hooks initialise
        plugins and logging. Uses self.preconf for pre-configuration,
        configuration. '''

        # ' xemacs syntax hack

        if kwargs:
            warnings.warn('Use .preconf instead of passing args to _getConfig')

        if self._conf:
            return self._conf
        conf_st = time.time()            

        if kwargs:
            for arg in ('fn', 'root', 'init_plugins', 'plugin_types',
                        'optparser', 'debuglevel', 'errorlevel',
                        'disabled_plugins', 'enabled_plugins'):
                if arg in kwargs:
                    setattr(self.preconf, arg, kwargs[arg])

        fn = self.preconf.fn
        root = self.preconf.root
        init_plugins = self.preconf.init_plugins
        plugin_types = self.preconf.plugin_types
        optparser = self.preconf.optparser
        debuglevel = self.preconf.debuglevel
        errorlevel = self.preconf.errorlevel
        disabled_plugins = self.preconf.disabled_plugins
        enabled_plugins = self.preconf.enabled_plugins
        syslog_ident    = self.preconf.syslog_ident
        syslog_facility = self.preconf.syslog_facility
        syslog_device   = self.preconf.syslog_device
        releasever = self.preconf.releasever
        arch = self.preconf.arch
        uuid = self.preconf.uuid
        
        if arch: # if preconf is setting an arch we need to pass that up
            self.arch.setup_arch(arch)
        else:
            arch = self.arch.canonarch

        # TODO: Remove this block when we no longer support configs outside
        # of /etc/yum/
        if fn == '/etc/yum/yum.conf' and not os.path.exists(fn):
            # Try the old default
            fn = '/etc/yum.conf'

        startupconf = config.readStartupConfig(fn, root)
        startupconf.arch = arch
        startupconf.basearch = self.arch.basearch
        if uuid:
            startupconf.uuid = uuid
        
        if startupconf.gaftonmode:
            global _
            _ = yum.i18n.dummy_wrapper

        if debuglevel != None:
            startupconf.debuglevel = debuglevel
        if errorlevel != None:
            startupconf.errorlevel = errorlevel
        if syslog_ident != None:
            startupconf.syslog_ident = syslog_ident
        if syslog_facility != None:
            startupconf.syslog_facility = syslog_facility
        if syslog_device != None:
            startupconf.syslog_device = syslog_device
        if releasever != None:
            startupconf.releasever = releasever

        self.doLoggingSetup(startupconf.debuglevel, startupconf.errorlevel,
                            startupconf.syslog_ident,
                            startupconf.syslog_facility,
                            startupconf.syslog_device)

        if init_plugins and startupconf.plugins:
            self.doPluginSetup(optparser, plugin_types, startupconf.pluginpath,
                    startupconf.pluginconfpath,disabled_plugins,enabled_plugins)

        self._conf = config.readMainConfig(startupconf)

        #  We don't want people accessing/altering preconf after it becomes
        # worthless. So we delete it, and thus. it'll raise AttributeError
        del self.preconf

        # Packages used to run yum...
        for pkgname in self.conf.history_record_packages:
            self.run_with_package_names.add(pkgname)

        # run the postconfig plugin hook
        self.plugins.run('postconfig')
        #  Note that Pungi has historically replaced _getConfig(), and it sets
        # up self.conf.yumvar but not self.yumvar ... and AFAIK nothing needs
        # to use YumBase.yumvar, so it's probably easier to just semi-deprecate
        # this (core now only uses YumBase.conf.yumvar).
        self.yumvar = self.conf.yumvar

        # who are we:
        self.conf.uid = os.geteuid()
        
        self.doFileLogSetup(self.conf.uid, self.conf.logfile)
        self.verbose_logger.debug('Config time: %0.3f' % (time.time() - conf_st))
        self.plugins.run('init')
        return self._conf
        

    def doLoggingSetup(self, debuglevel, errorlevel,
                       syslog_ident=None, syslog_facility=None,
                       syslog_device='/dev/log'):
        '''
        Perform logging related setup.

        @param debuglevel: Debug logging level to use.
        @param errorlevel: Error logging level to use.
        '''
        logginglevels.doLoggingSetup(debuglevel, errorlevel,
                                     syslog_ident, syslog_facility,
                                     syslog_device)

    def doFileLogSetup(self, uid, logfile):
        logginglevels.setFileLog(uid, logfile)

    def getReposFromConfigFile(self, repofn, repo_age=None, validate=None):
        """read in repositories from a config .repo file"""

        if repo_age is None:
            repo_age = os.stat(repofn)[8]
        
        confpp_obj = ConfigPreProcessor(repofn, vars=self.conf.yumvar)
        parser = ConfigParser()
        try:
            parser.readfp(confpp_obj)
        except ParsingError as e:
            msg = str(e)
            raise Errors.ConfigError(msg)#raise Errors.ConfigError, msg

        # Check sections in the .repo file that was just slurped up
        for section in parser.sections():

            if section in ['main', 'installed']:
                continue

            # Check the repo.id against the valid chars
            bad = None
            for byte in section:
                if byte in string.ascii_letters:
                    continue
                if byte in string.digits:
                    continue
                if byte in "-_.:":
                    continue
                
                bad = byte
                break

            if bad:
                self.logger.warning("Bad id for repo: %s, byte = %s %d" %
                                    (section, bad, section.find(bad)))
                continue

            try:
                thisrepo = self.readRepoConfig(parser, section)
            except (Errors.RepoError, Errors.ConfigError) as e:
                self.logger.warning(e)
                continue
            else:
                thisrepo.repo_config_age = repo_age
                thisrepo.repofile = repofn

            if thisrepo.id in self.repo_setopts:
                for opt in self.repo_setopts[thisrepo.id].items:
                    setattr(thisrepo, opt, getattr(self.repo_setopts[thisrepo.id], opt))
                    
            if validate and not validate(thisrepo):
                continue
                    
            # Got our list of repo objects, add them to the repos
            # collection
            try:
                self._repos.add(thisrepo)
            except Errors.RepoError as e:
                self.logger.warning(e)
        
    def getReposFromConfig(self):
        """read in repositories from config main and .repo files"""

        # Read .repo files from directories specified by the reposdir option
        # (typically /etc/yum/repos.d)
        repo_config_age = self.conf.config_file_age
        
        # Get the repos from the main yum.conf file
        self.getReposFromConfigFile(self.conf.config_file_path, repo_config_age)

        for reposdir in self.conf.reposdir:
            # this check makes sure that our dirs exist properly.
            # if they aren't in the installroot then don't prepend the installroot path
            # if we don't do this then anaconda likes to not  work.
            if os.path.exists(self.conf.installroot+'/'+reposdir):
                reposdir = self.conf.installroot + '/' + reposdir

            if os.path.isdir(reposdir):
                for repofn in sorted(glob.glob('%s/*.repo' % reposdir)):
                    thisrepo_age = os.stat(repofn)[8]
                    if thisrepo_age < repo_config_age:
                        thisrepo_age = repo_config_age
                    self.getReposFromConfigFile(repofn, repo_age=thisrepo_age)

    def readRepoConfig(self, parser, section):
        '''Parse an INI file section for a repository.

        @param parser: ConfParser or similar to read INI file values from.
        @param section: INI file section to read.
        @return: YumRepository instance.
        '''
        repo = yumRepo.YumRepository(section)
        repo.populate(parser, section, self.conf)

        # Ensure that the repo name is set
        if not repo.name:
            repo.name = section
            self.logger.error(_('Repository %r is missing name in configuration, '
                    'using id') % section)
        repo.name = to_unicode(repo.name)

        # Set attributes not from the config file
        repo.basecachedir = self.conf.cachedir
        repo.yumvar.update(self.conf.yumvar)
        repo.cfg = parser

        return repo

    def disablePlugins(self):
        '''Disable yum plugins
        '''
        self.plugins = plugins.DummyYumPlugins()
    
    def doPluginSetup(self, optparser=None, plugin_types=None, searchpath=None,
            confpath=None,disabled_plugins=None,enabled_plugins=None):
        '''Initialise and enable yum plugins. 

        Note: _getConfig() will initialise plugins if instructed to. Only
        call this method directly if not calling _getConfig() or calling
        doConfigSetup(init_plugins=False).

        @param optparser: The OptionParser instance for this run (optional)
        @param plugin_types: A sequence specifying the types of plugins to load.
            This should be a sequence containing one or more of the
            yum.plugins.TYPE_...  constants. If None (the default), all plugins
            will be loaded.
        @param searchpath: A list of directories to look in for plugins. A
            default will be used if no value is specified.
        @param confpath: A list of directories to look in for plugin
            configuration files. A default will be used if no value is
            specified.
        @param disabled_plugins: Plugins to be disabled    
        @param enabled_plugins: Plugins to be enabled
        '''
        if isinstance(self.plugins, plugins.YumPlugins):
            raise RuntimeError(_("plugins already initialised"))

        self.plugins = plugins.YumPlugins(self, searchpath, optparser,
                plugin_types, confpath, disabled_plugins, enabled_plugins)

    
    def doRpmDBSetup(self):
        warnings.warn(_('doRpmDBSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getRpmDB()
    
    def _getRpmDB(self):
        """sets up a holder object for important information from the rpmdb"""

        if self._rpmdb is None:
            rpmdb_st = time.time()
            self.verbose_logger.log(logginglevels.DEBUG_4,
                                    _('Reading Local RPMDB'))
            self._rpmdb = rpmsack.RPMDBPackageSack(root=self.conf.installroot,
                                                   releasever=self.conf.yumvar['releasever'],
                                                   persistdir=self.conf.persistdir)
            self.verbose_logger.debug('rpmdb time: %0.3f' % (time.time() - rpmdb_st))
        return self._rpmdb

    def closeRpmDB(self):
        """closes down the instances of the rpmdb we have wangling around"""
        if self._rpmdb is not None:
            self._rpmdb.ts = None
            self._rpmdb.dropCachedData()
        self._rpmdb = None
        self._ts = None
        self._tsInfo = None
        self._up = None
        self.comps = None
    
    def _deleteTs(self):
        del self._ts
        self._ts = None

    def doRepoSetup(self, thisrepo=None):
        warnings.warn(_('doRepoSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getRepos(thisrepo, True)

    def _getRepos(self, thisrepo=None, doSetup = False):
        """ For each enabled repository set up the basics of the repository. """
        if hasattr(self, 'prerepoconf'):
            self.conf # touch the config class first

            self.getReposFromConfig()

            # Recursion
            prerepoconf = self.prerepoconf
            del self.prerepoconf

            self.repos.setProgressBar(prerepoconf.progressbar)
            self.repos.callback = prerepoconf.callback
            self.repos.setFailureCallback(prerepoconf.failure_callback)
            self.repos.setInterruptCallback(prerepoconf.interrupt_callback)
            self.repos.confirm_func = prerepoconf.confirm_func
            self.repos.gpg_import_func = prerepoconf.gpg_import_func
            if prerepoconf.cachedir is not None:
                self.repos.setCacheDir(prerepoconf.cachedir)
            if prerepoconf.cache is not None:
                self.repos.setCache(prerepoconf.cache)


        if doSetup:
            repo_st = time.time()        
            self._repos.doSetup(thisrepo)
            self.verbose_logger.debug('repo time: %0.3f' % (time.time() - repo_st))        
        return self._repos

    def _delRepos(self):
        del self._repos
        self._repos = RepoStorage(self)
    
    def doSackSetup(self, archlist=None, thisrepo=None):
        warnings.warn(_('doSackSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getSacks(archlist=archlist, thisrepo=thisrepo)
        
    def _getSacks(self, archlist=None, thisrepo=None):
        """populates the package sacks for information from our repositories,
           takes optional archlist for archs to include"""

        # FIXME: Fist of death ... normally we'd do either:
        #
        # 1. use self._pkgSack is not None, and only init. once.
        # 2. auto. correctly re-init each time a repo is added/removed
        #
        # ...we should probably just smeg it and do #2, but it's hard and will
        # probably break something (but it'll "fix" excludes).
        #  #1 can't be done atm. because we did self._pkgSack and external
        # tools now rely on being able to create an empty sack and then have it
        # auto. re-init when they add some stuff. So we add a bit more "clever"
        # and don't setup the pkgSack to not be None when it's empty. This means
        # we skip excludes/includes/etc. ... but there's no packages, so
        # hopefully that's ok.
        if self._pkgSack is not None and thisrepo is None:
            return self._pkgSack
        
        if thisrepo is None:
            repos = 'enabled'
        else:
            repos = self.repos.findRepos(thisrepo)
        
        self.verbose_logger.debug(_('Setting up Package Sacks'))
        sack_st = time.time()
        if not archlist:
            archlist = self.arch.archlist
        
        archdict = {}
        for arch in archlist:
            archdict[arch] = 1
        
        self.repos.getPackageSack().setCompatArchs(archdict)
        self.repos.populateSack(which=repos)
        if not self.repos.getPackageSack():
            return self.repos.getPackageSack() # ha ha, see above
        self._pkgSack = self.repos.getPackageSack()
        
        self.excludePackages()
        self._pkgSack.excludeArchs(archlist)
        
        #FIXME - this could be faster, too.
        if repos == 'enabled':
            repos = self.repos.listEnabled()
        for repo in repos:
            self.includePackages(repo)
            self.excludePackages(repo)
        self.plugins.run('exclude')
        self._pkgSack.buildIndexes()

        # now go through and kill pkgs based on pkg.repo.cost()
        self.costExcludePackages()
        self.verbose_logger.debug('pkgsack time: %0.3f' % (time.time() - sack_st))
        return self._pkgSack
    
    
    def _delSacks(self):
        """reset the package sacks back to zero - making sure to nuke the ones
           in the repo objects, too - where it matters"""
           
        # nuke the top layer
        
        self._pkgSack = None
           
        for repo in self.repos.repos.values():
            if hasattr(repo, '_resetSack'):
                repo._resetSack()
            else:
                warnings.warn(_('repo object for repo %s lacks a _resetSack method\n') +
                        _('therefore this repo cannot be reset.\n'),
                        Errors.YumFutureDeprecationWarning, stacklevel=2)
            
           
    def doUpdateSetup(self):
        warnings.warn(_('doUpdateSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getUpdates()
        
    def _getUpdates(self):
        """setups up the update object in the base class and fills out the
           updates, obsoletes and others lists"""
        
        if self._up:
            return self._up
        
        self.verbose_logger.debug(_('Building updates object'))

        up_st = time.time()

        self._up = rpmUtils.updates.Updates(self.rpmdb.simplePkgList(), self.pkgSack.simplePkgList())
        if self.conf.debuglevel >= 7:
            self._up.debug = 1
        
        if self.conf.obsoletes:
            obs_init = time.time()    
            #  Note: newest=True here is semi-required for repos. with multiple
            # versions. The problem is that if pkgA-2 _accidentally_ obsoletes
            # pkgB-1, and we keep all versions, we want to release a pkgA-3
            # that doesn't do the obsoletes ... and thus. not obsolete pkgB-1.
            self._up.rawobsoletes = self.pkgSack.returnObsoletes(newest=True)
            self.verbose_logger.debug('up:Obs Init time: %0.3f' % (time.time() - obs_init))

        self._up.myarch = self.arch.canonarch
        self._up._is_multilib = self.arch.multilib
        self._up._archlist = self.arch.archlist
        self._up._multilib_compat_arches = self.arch.compatarches
        self._up.exactarch = self.conf.exactarch
        self._up.exactarchlist = self.conf.exactarchlist
        up_pr_st = time.time()
        self._up.doUpdates()
        self.verbose_logger.debug('up:simple updates time: %0.3f' % (time.time() - up_pr_st))

        if self.conf.obsoletes:
            obs_st = time.time()
            self._up.doObsoletes()
            self.verbose_logger.debug('up:obs time: %0.3f' % (time.time() - obs_st))

        cond_up_st = time.time()                    
        self._up.condenseUpdates()
        self.verbose_logger.debug('up:condense time: %0.3f' % (time.time() - cond_up_st))
        self.verbose_logger.debug('updates time: %0.3f' % (time.time() - up_st))        
        return self._up
    
    def doGroupSetup(self):
        warnings.warn(_('doGroupSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        self.comps = None
        return self._getGroups()

    def _setGroups(self, val):
        if val is None:
            # if we unset the comps object, we need to undo which repos have
            # been added to the group file as well
            if self._repos:
                for repo in self._repos.listGroupsEnabled():
                    repo.groups_added = False
        self._comps = val
    
    def _getGroups(self):
        """create the groups object that will store the comps metadata
           finds the repos with groups, gets their comps data and merge it
           into the group object"""
        
        if self._comps:
            return self._comps

        group_st = time.time()            
        self.verbose_logger.log(logginglevels.DEBUG_4,
                                _('Getting group metadata'))
        reposWithGroups = []
        self.repos.doSetup()
        for repo in self.repos.listGroupsEnabled():
            if repo.groups_added: # already added the groups from this repo
                reposWithGroups.append(repo)
                continue
                
            if not repo.ready():
                raise Errors.RepoError("Repository '%s' not yet setup" % repo)#, "Repository '%s' not yet setup" % repo
            try:
                groupremote = repo.getGroupLocation()
            except Errors.RepoMDError as e:
                pass
            else:
                reposWithGroups.append(repo)
        
        # now we know which repos actually have groups files.
        overwrite = self.conf.overwrite_groups
        self._comps = comps.Comps(overwrite_groups = overwrite)

        for repo in reposWithGroups:
            if repo.groups_added: # already added the groups from this repo
                continue
                
            self.verbose_logger.log(logginglevels.DEBUG_4,
                _('Adding group file from repository: %s'), repo)
            groupfile = repo.getGroups()
            # open it up as a file object so iterparse can cope with our compressed file
            if groupfile:
                groupfile = misc.decompress(groupfile)
                
            try:
                self._comps.add(groupfile)
            except (Errors.GroupsError,Errors.CompsException) as e:
                msg = _('Failed to add groups file for repository: %s - %s') % (repo, str(e))
                self.logger.critical(msg)
            else:
                repo.groups_added = True

        if self._comps.compscount == 0:
            raise Errors.GroupsError(_('No Groups Available in any repository'))#, _('No Groups Available in any repository')

        self._comps.compile(self.rpmdb.simplePkgList())
        self.verbose_logger.debug('group time: %0.3f' % (time.time() - group_st))                
        return self._comps

    def _getTags(self):
        """ create the tags object used to search/report from the pkgtags 
            metadata"""
        
        tag_st = time.time()
        self.verbose_logger.log(logginglevels.DEBUG_4,
                                _('Getting pkgtags metadata'))
        
        if self._tags is None:
            self._tags = yum.pkgtag_db.PackageTags()
           
            for repo in self.repos.listEnabled():
                if 'pkgtags' not in repo.repoXML.fileTypes():
                    continue

                self.verbose_logger.log(logginglevels.DEBUG_4,
                    _('Adding tags from repository: %s'), repo)
                
                # fetch the sqlite tagdb
                try:
                    tag_md = repo.retrieveMD('pkgtags')
                    tag_sqlite  = yum.misc.decompress(tag_md)
                    # feed it into _tags.add()
                    self._tags.add(repo.id, tag_sqlite)
                except (Errors.RepoError, Errors.PkgTagsError) as e:
                    msg = _('Failed to add Pkg Tags for repository: %s - %s') % (repo, str(e))
                    self.logger.critical(msg)
                    
                
        self.verbose_logger.debug('tags time: %0.3f' % (time.time() - tag_st))
        return self._tags
        
    def _getHistory(self):
        """auto create the history object that to access/append the transaction
           history information. """
        if self._history is None:
            pdb_path = self.conf.persistdir + "/history"
            self._history = yum.history.YumHistory(root=self.conf.installroot,
                                                   db_path=pdb_path)
        return self._history
    
    # properties so they auto-create themselves with defaults
    repos = property(fget=lambda self: self._getRepos(),
                     fset=lambda self, value: setattr(self, "_repos", value),
                     fdel=lambda self: self._delRepos(),
                     doc="Repo Storage object - object of yum repositories")
    pkgSack = property(fget=lambda self: self._getSacks(),
                       fset=lambda self, value: setattr(self, "_pkgSack", value),
                       fdel=lambda self: self._delSacks(),
                       doc="Package sack object - object of yum package objects")
    conf = property(fget=lambda self: self._getConfig(),
                    fset=lambda self, value: setattr(self, "_conf", value),
                    fdel=lambda self: setattr(self, "_conf", None),
                    doc="Yum Config Object")
    rpmdb = property(fget=lambda self: self._getRpmDB(),
                     fset=lambda self, value: setattr(self, "_rpmdb", value),
                     fdel=lambda self: setattr(self, "_rpmdb", None),
                     doc="RpmSack object")
    tsInfo = property(fget=lambda self: self._getTsInfo(), 
                      fset=lambda self,value: self._setTsInfo(value), 
                      fdel=lambda self: self._delTsInfo(),
                      doc="Transaction Set information object")
    ts = property(fget=lambda self: self._getActionTs(), 
                  fdel=lambda self: self._deleteTs(),
                  doc="TransactionSet object")
    up = property(fget=lambda self: self._getUpdates(),
                  fset=lambda self, value: setattr(self, "_up", value),
                  fdel=lambda self: setattr(self, "_up", None),
                  doc="Updates Object")
    comps = property(fget=lambda self: self._getGroups(),
                     fset=lambda self, value: self._setGroups(value),
                     fdel=lambda self: setattr(self, "_comps", None),
                     doc="Yum Component/groups object")
    history = property(fget=lambda self: self._getHistory(),
                       fset=lambda self, value: setattr(self, "_history",value),
                       fdel=lambda self: setattr(self, "_history", None),
                       doc="Yum History Object")

    pkgtags = property(fget=lambda self: self._getTags(),
                       fset=lambda self, value: setattr(self, "_tags",value),
                       fdel=lambda self: setattr(self, "_tags", None),
                       doc="Yum Package Tags Object")
    
    
    def doSackFilelistPopulate(self):
        """convenience function to populate the repos with the filelist metadata
           it also is simply to only emit a log if anything actually gets populated"""
        
        necessary = False
        
        # I can't think of a nice way of doing this, we have to have the sack here
        # first or the below does nothing so...
        if self.pkgSack:
            for repo in self.repos.listEnabled():
                if repo in repo.sack.added:
                    if 'filelists' in repo.sack.added[repo]:
                        continue
                    else:
                        necessary = True
                else:
                    necessary = True

        if necessary:
            msg = _('Importing additional filelist information')
            self.verbose_logger.log(logginglevels.INFO_2, msg)
            self.repos.populateSack(mdtype='filelists')
           
    def yumUtilsMsg(self, func, prog):
        """ Output a message that the tool requires the yum-utils package,
            if not installed. """
        if self.rpmdb.contains(name="yum-utils"):
            return

        hibeg, hiend = "", ""
        if hasattr(self, 'term'):
            hibeg, hiend = self.term.MODE['bold'], self.term.MODE['normal']

        func(_("The program %s%s%s is found in the yum-utils package.") %
             (hibeg, prog, hiend))

    def buildTransaction(self, unfinished_transactions_check=True):
        """go through the packages in the transaction set, find them in the
           packageSack or rpmdb, and pack up the ts accordingly"""
        if (unfinished_transactions_check and
            misc.find_unfinished_transactions(yumlibpath=self.conf.persistdir)):
            msg = _('There are unfinished transactions remaining. You might ' \
                    'consider running yum-complete-transaction first to finish them.' )
            self.logger.critical(msg)
            self.yumUtilsMsg(self.logger.critical, "yum-complete-transaction")
            time.sleep(3)
        
        # XXX - we could add a conditional here to avoid running the plugins and 
        # limit_installonly_pkgs, etc - if we're being run from yum-complete-transaction
        # and don't want it to happen. - skv
        
        self.plugins.run('preresolve')
        ds_st = time.time()

        (rescode, restring) = self.resolveDeps()
        self._limit_installonly_pkgs()
        
        #  We _must_ get rid of all the used tses before we go on, so that C-c
        # works for downloads / mirror failover etc.
        kern_pkgtup = None
        if rescode == 2 and self.conf.protected_packages:
            kern_pkgtup = misc.get_running_kernel_pkgtup(self.rpmdb.ts)
        self.rpmdb.ts = None

        # do the skip broken magic, if enabled and problems exist
        (rescode, restring) = self._doSkipBroken(rescode, restring)

        self.plugins.run('postresolve', rescode=rescode, restring=restring)

        if self.tsInfo.changed:
            (rescode, restring) = self.resolveDeps(rescode == 1)
            # If transaction was changed by postresolve plugins then we should run skipbroken again
            (rescode, restring) = self._doSkipBroken(rescode, restring, clear_skipped=False )

        if self.tsInfo.pkgSack is not None: # rm Transactions don't have pkgSack
            self.tsInfo.pkgSack.dropCachedData()

        #  This is a version of the old "protect-packages" plugin, it allows
        # you to erase duplicates and do remove+install.
        #  But we don't allow you to turn it off!:)
        protect_states = [TS_OBSOLETED, TS_ERASE]
        txmbrs = []
        if rescode == 2 and self.conf.protected_packages:
            protected = set(self.conf.protected_packages)
            txmbrs = self.tsInfo.getMembersWithState(None, protect_states)
        bad_togo = {}
        for txmbr in txmbrs:
            if kern_pkgtup is not None and txmbr.pkgtup == kern_pkgtup:
                pass
            elif txmbr.name not in protected:
                continue
            if txmbr.name not in bad_togo:
                bad_togo[txmbr.name] = []
            bad_togo[txmbr.name].append(txmbr.pkgtup)
        for ipkg in self.rpmdb.searchNames(bad_togo.keys()):
            if (kern_pkgtup is not None and ipkg.name == kern_pkgtup[0] and
                kern_pkgtup in bad_togo[kern_pkgtup[0]]):
                continue # If "running kernel" matches, it's always bad.
            if ipkg.name not in bad_togo:
                continue
            # If there is at least one version not being removed, allow it
            if ipkg.pkgtup not in bad_togo[ipkg.name]:
                del bad_togo[ipkg.name]
        for pkgname in bad_togo.keys():
            if (kern_pkgtup is not None and pkgname == kern_pkgtup[0] and
                kern_pkgtup in bad_togo[kern_pkgtup[0]]):
                continue # If "running kernel" matches, it's always bad.
            for txmbr in self.tsInfo.matchNaevr(name=pkgname):
                if txmbr.name not in bad_togo:
                    continue
                if txmbr.pkgtup in bad_togo[ipkg.name]:
                    continue
                # If we are installing one version we aren't removing, allow it
                if txmbr.output_state in TS_INSTALL_STATES:
                    del bad_togo[ipkg.name]

        if bad_togo:
            rescode = 1
            restring = []
            for pkgname in sorted(bad_togo):
                restring.append(_('Trying to remove "%s", which is protected') %
                                pkgname)

        self.rpmdb.dropCachedData()

        self.verbose_logger.debug('Depsolve time: %0.3f' % (time.time() - ds_st))
        return rescode, restring

    def _doSkipBroken(self,rescode, restring, clear_skipped=True):
        ''' do skip broken if it is enabled '''
        # if depsolve failed and skipbroken is enabled
        # The remove the broken packages from the transactions and
        # Try another depsolve
        if self.conf.skip_broken and rescode==1:
            if clear_skipped:
                self.skipped_packages = []    # reset the public list of skipped packages.
            sb_st = time.time()
            rescode, restring = self._skipPackagesWithProblems(rescode, restring)
            self._printTransaction()        
            self.verbose_logger.debug('Skip-Broken time: %0.3f' % (time.time() - sb_st))
        return (rescode, restring)
            

    def _skipPackagesWithProblems(self, rescode, restring):
        ''' Remove the packages with depsolve errors and depsolve again '''

        def _remove(po, depTree, toRemove):
            if not po:
                return
            self._getPackagesToRemove(po, depTree, toRemove)
            # Only remove non installed packages from pkgSack
            _remove_from_sack(po)

        def _remove_from_sack(po):
            # get all compatible arch packages from pkgSack
            # we need to remove them too so i386 packages are not 
            # dragged in when a x86_64 is skipped.
            pkgs = self._getPackagesToRemoveAllArch(po)
            for pkg in pkgs:
                if not po.repoid == 'installed' and pkg not in removed_from_sack:             
                    self.verbose_logger.debug('SKIPBROKEN: removing %s from pkgSack & updates' % str(po))
                    self.pkgSack.delPackage(pkg)
                    self.up.delPackage(pkg.pkgtup)
                    removed_from_sack.add(pkg)

        # Keep removing packages & Depsolve until all errors is gone
        # or the transaction is empty
        count = 0
        skipped_po = set()
        removed_from_sack = set()
        orig_restring = restring    # Keep the old error messages 
        looping = 0 
        while (len(self.po_with_problems) > 0 and rescode == 1):
            count += 1
            #  Remove all the rpmdb cache data, this is somewhat heavy handed
            # but easier than removing/altering specific bits of the cache ...
            # and skip-broken shouldn't care too much about speed.
            self.rpmdb.transactionReset()
            self.installedFileRequires = None # Kind of hacky
            self.verbose_logger.debug(_("Skip-broken round %i"), count)
            self._printTransaction()        
            depTree = self._buildDepTree()
            startTs = set(self.tsInfo)
            toRemove = set()
            for po,wpo,err in self.po_with_problems:
                # check if the problem is caused by a package in the transaction
                if not self.tsInfo.exists(po.pkgtup):
                    _remove(wpo, depTree, toRemove)
                else:
                    _remove(po,  depTree, toRemove)
            for po in toRemove:
                skipped = self._skipFromTransaction(po)
                for skip in skipped:
                    skipped_po.add(skip)
                    # make sure we get the compat arch packages skip from pkgSack and up too.
                    if skip not in removed_from_sack and skip.repoid == 'installed':
                        _remove_from_sack(skip)
            # Nothing was removed, so we still got a problem
             # the first time we get here we reset the resolved members of
             # tsInfo and takes a new run all members in the current transaction
            if not toRemove: 
                looping += 1
                if looping > 2:
                    break # Bail out
                else:
                    self.verbose_logger.debug('SKIPBROKEN: resetting already resolved packages (no packages to skip)' )
                    self.tsInfo.resetResolved(hard=True)
            rescode, restring = self.resolveDeps(True)
            endTs = set(self.tsInfo)
             # Check if tsInfo has changes since we started to skip packages
             # if there is no changes then we got a loop.
             # the first time we get here we reset the resolved members of
             # tsInfo and takes a new run all members in the current transaction
            if startTs-endTs == set():
                looping += 1
                if looping > 2:
                    break # Bail out
                else:
                    self.verbose_logger.debug('SKIPBROKEN: resetting already resolved packages (transaction not changed)' )
                    self.tsInfo.resetResolved(hard=True)
            else: 
                # Reset the looping counter, because it is only a loop if the same transaction is
                # unchanged two times in row, not if it has been unchanged in a early stage.
                looping = 0 
                    
            # if we are all clear, then we have to check that the whole current transaction 
            # can complete the depsolve without error, because the packages skipped
            # can have broken something that passed the tests earlier.
            # FIXME: We need do this in a better way.
            if rescode != 1:
                self.verbose_logger.debug('SKIPBROKEN: sanity check the current transaction' )
                self.tsInfo.resetResolved(hard=True)
                self._checkMissingObsoleted() # This is totally insane, but needed :(
                self._checkUpdatedLeftovers() # Cleanup updated leftovers
                rescode, restring = self.resolveDeps()
        if rescode != 1:
            self.verbose_logger.debug(_("Skip-broken took %i rounds "), count)
            self.verbose_logger.info(_('\nPackages skipped because of dependency problems:'))
            skipped_list = [p for p in skipped_po]
            skipped_list.sort()
            for po in skipped_list:
                msg = _("    %s from %s") % (str(po),po.repo.id)
                self.verbose_logger.info(msg)
            self.skipped_packages.extend(skipped_list)   # make the skipped packages public
        else:
            # If we cant solve the problems the show the original error messages.
            self.verbose_logger.info("Skip-broken could not solve problems")
            return 1, orig_restring
        return rescode, restring

    def _checkMissingObsoleted(self):
        """ 
        If multiple packages is obsoleting the same package
        then the TS_OBSOLETED can get removed from the transaction
        so we must make sure that they, exist and else create them
        """
        for txmbr in self.tsInfo.getMembersWithState(None, [TS_OBSOLETING,TS_OBSOLETED]):
            for pkg in txmbr.obsoletes:
                if not self.tsInfo.exists(pkg.pkgtup):
                    obs = self.tsInfo.addObsoleted(pkg,txmbr.po)
                    self.verbose_logger.debug('SKIPBROKEN: Added missing obsoleted %s (%s)' % (pkg,txmbr.po) )
            for pkg in txmbr.obsoleted_by:
                # check if the obsoleting txmbr is in the transaction
                # else remove the obsoleted txmbr
                # it clean out some really wierd cases
                if not self.tsInfo.exists(pkg.pkgtup):
                    self.verbose_logger.debug('SKIPBROKEN: Remove extra obsoleted %s (%s)' % (txmbr.po,pkg) )
                    self.tsInfo.remove(txmbr.po.pkgtup)

    def _checkUpdatedLeftovers(self):
        """ 
        If multiple packages is updated the same package
        and this package get removed because of an dep issue
        then make sure that all the TS_UPDATED get removed.
        """
        for txmbr in self.tsInfo.getMembersWithState(None, [TS_UPDATED]):
            for pkg in txmbr.updated_by:
                # check if the updating txmbr is in the transaction
                # else remove the updated txmbr
                # it clean out some really wierd cases with dupes installed on the system
                if not self.tsInfo.exists(pkg.pkgtup):
                    self.verbose_logger.debug('SKIPBROKEN: Remove extra updated %s (%s)' % (txmbr.po,pkg) )
                    self.tsInfo.remove(txmbr.po.pkgtup)

    def _getPackagesToRemoveAllArch(self,po):
        ''' get all compatible arch packages in pkgSack'''
        pkgs = []
        if self.arch.multilib:
            n,a,e,v,r = po.pkgtup
            # skip for all compat archs
            for a in self.arch.archlist:
                pkgtup = (n,a,e,v,r)
                matched = self.pkgSack.searchNevra(n,e,v,r,a) 
                pkgs.extend(matched)
        else:
            pkgs.append(po)
        return pkgs   
        
                
                
        

    def _skipFromTransaction(self,po):
        skipped =  []
        n,a,e,v,r = po.pkgtup
        # skip for all compat archs
        for a in self.arch.archlist:
            pkgtup = (n,a,e,v,r)
            if self.tsInfo.exists(pkgtup):
                for txmbr in self.tsInfo.getMembers(pkgtup):
                    pkg = txmbr.po
                    skip = self._removePoFromTransaction(pkg)
                    skipped.extend(skip)
        return skipped

    def _removePoFromTransaction(self,po):
        skip =  []
        if self.tsInfo.exists(po.pkgtup):
            self.verbose_logger.debug('SKIPBROKEN: removing %s from transaction' % str(po))
            self.tsInfo.remove(po.pkgtup)
            if not po.repoid == 'installed':
                skip.append(po)
        return skip 
              
    def _buildDepTree(self):
        ''' create a dictionary with po and deps '''
        depTree = { }
        for txmbr in self.tsInfo:
            for dep in txmbr.depends_on:
                depTree.setdefault(dep, []).append(txmbr.po)
        # self._printDepTree(depTree)
        return depTree

    def _printDepTree(self, tree):
        for pkg, l in tree.iteritems():
            print(pkg)
            for p in l:
                print("\t"), p

    def _printTransaction(self):
        #transaction set states
        state = { TS_UPDATE     : "update",
                  TS_INSTALL    : "install",
                  TS_TRUEINSTALL: "trueinstall",
                  TS_ERASE      : "erase",
                  TS_OBSOLETED  : "obsoleted",
                  TS_OBSOLETING : "obsoleting",
                  TS_AVAILABLE  : "available",
                  TS_UPDATED    : "updated"}

        self.verbose_logger.log(logginglevels.DEBUG_2,"TSINFO: Current Transaction : %i member(s) " % len(self.tsInfo))
        for txmbr in sorted(self.tsInfo):
            msg = "  %-11s : %s " % (state[txmbr.output_state],txmbr.po)
            self.verbose_logger.log(logginglevels.DEBUG_2, msg)
            for po,rel in sorted(txmbr.relatedto):
                msg = "                   %s : %s" % (rel,po)
                self.verbose_logger.log(logginglevels.DEBUG_2, msg)
                
                                    
    def _getPackagesToRemove(self,po,deptree,toRemove):
        '''
        get the (related) pos to remove.
        '''
        toRemove.add(po)
        for txmbr in self.tsInfo.getMembers(po.pkgtup):
            for pkg in (txmbr.updates + txmbr.obsoletes):
                toRemove.add(pkg)
                self._getDepsToRemove(pkg, deptree, toRemove)
        self._getDepsToRemove(po, deptree, toRemove)

    def _getDepsToRemove(self,po, deptree, toRemove):
        for dep in deptree.get(po, []): # Loop trough all deps of po
            for txmbr in self.tsInfo.getMembers(dep.pkgtup):
                for pkg in (txmbr.updates + txmbr.obsoletes):
                    toRemove.add(pkg)
            toRemove.add(dep)
            self._getDepsToRemove(dep, deptree, toRemove)

    def _rpmdb_warn_checks(self, out=None, warn=True, chkcmd=None, header=None,
                           ignore_pkgs=[]):
        if out is None:
            out = self.logger.warning
        if chkcmd is None:
            chkcmd = ['dependencies', 'duplicates']
        if header is None:
            # FIXME: _N()
            msg = _("** Found %d pre-existing rpmdb problem(s),"
                    " 'yum check' output follows:")
            header = lambda problems: not problems or out(msg % problems)
        if warn:
            out(_('Warning: RPMDB altered outside of yum.'))

        if type(chkcmd) in (type([]), type(set())):
            chkcmd = set(chkcmd)
        else:
            chkcmd = set([chkcmd])

        ignore_pkgtups = set((pkg.pkgtup for pkg in ignore_pkgs))

        rc = 0
        probs = []
        if chkcmd.intersection(set(('all', 'dependencies'))):
            prob2ui = {'requires' : _('missing requires'),
                       'conflicts' : _('installed conflict')}
            for prob in self.rpmdb.check_dependencies():
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                if prob.problem == 'conflicts':
                    found = True # all the conflicting pkgs have to be ignored
                    for res in prob.conflicts:
                        if res.pkgtup not in ignore_pkgtups:
                            found = False
                            break
                    if found:
                        continue
                probs.append(prob)

        if chkcmd.intersection(set(('all', 'duplicates'))):
            iopkgs = set(self.conf.installonlypkgs)
            for prob in self.rpmdb.check_duplicates(iopkgs):
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                if prob.duplicate.pkgtup in ignore_pkgtups:
                    continue
                probs.append(prob)

        if chkcmd.intersection(set(('all', 'obsoleted'))):
            for prob in self.rpmdb.check_obsoleted():
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                if prob.obsoleter.pkgtup in ignore_pkgtups:
                    continue
                probs.append(prob)

        if chkcmd.intersection(set(('all', 'provides'))):
            for prob in self.rpmdb.check_provides():
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                probs.append(prob)

        header(len(probs))
        for prob in sorted(probs):
            out(prob)

        return probs

    def runTransaction(self, cb):
        """takes an rpm callback object, performs the transaction"""

        self.plugins.run('pretrans')

        #  We may want to put this other places, eventually, but for now it's
        # good as long as we get it right for history.
        for repo in self.repos.listEnabled():
            if repo._xml2sqlite_local:
                self.run_with_package_names.add('yum-metadata-parser')
                break

        if self.conf.history_record and not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
            using_pkgs_pats = list(self.run_with_package_names)
            using_pkgs = self.rpmdb.returnPackages(patterns=using_pkgs_pats)
            rpmdbv  = self.rpmdb.simpleVersion(main_only=True)[0]
            lastdbv = self.history.last()
            if lastdbv is not None:
                lastdbv = lastdbv.end_rpmdbversion
            rpmdb_problems = []
            if lastdbv is None or rpmdbv != lastdbv:
                txmbrs = self.tsInfo.getMembersWithState(None, TS_REMOVE_STATES)
                ignore_pkgs = [txmbr.po for txmbr in txmbrs]
                output_warn = lastdbv is not None
                rpmdb_problems = self._rpmdb_warn_checks(warn=output_warn,
                                                        ignore_pkgs=ignore_pkgs)
            cmdline = None
            if hasattr(self, 'args') and self.args:
                cmdline = ' '.join(self.args)
            elif hasattr(self, 'cmds') and self.cmds:
                cmdline = ' '.join(self.cmds)
            self.history.beg(rpmdbv, using_pkgs, list(self.tsInfo),
                             self.skipped_packages, rpmdb_problems, cmdline)
            # write out our config and repo data to additional history info
            self._store_config_in_history()
            
            self.plugins.run('historybegin')
        #  Just before we update the transaction, update what we think the
        # rpmdb will look like. This needs to be done before the run, so that if
        # "something" happens and the rpmdb is different from what we think it
        # will be we store what we thought, not what happened (so it'll be an
        # invalid cache).
        self.rpmdb.transactionResultVersion(self.tsInfo.futureRpmDBVersion())

        errors = self.ts.run(cb.callback, '')
        # ts.run() exit codes are, hmm, "creative": None means all ok, empty 
        # list means some errors happened in the transaction and non-empty 
        # list that there were errors preventing the ts from starting...
        
        # make resultobject - just a plain yumgenericholder object
        resultobject = misc.GenericHolder()
        resultobject.return_code = 0
        if errors is None:
            pass
        elif len(errors) == 0:
            errstring = _('Warning: scriptlet or other non-fatal errors occurred during transaction.')
            self.verbose_logger.debug(errstring)
            resultobject.return_code = 1
        else:
            if self.conf.history_record and not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
                herrors = [to_unicode(to_str(x)) for x in errors]
                self.plugins.run('historyend')                
                self.history.end(rpmdbv, 2, errors=herrors)

                
            self.logger.critical(_("Transaction couldn't start:"))
            for e in errors:
                self.logger.critical(e[0]) # should this be 'to_unicoded'?
            raise Errors.YumRPMTransError(msg=_("Could not run transaction."),
                                          errors=errors)

                          
        if not self.conf.keepcache:
            self.cleanUsedHeadersPackages()
        
        for i in ('ts_all_fn', 'ts_done_fn'):
            if hasattr(cb, i):
                fn = getattr(cb, i)
                try:
                    misc.unlink_f(fn)
                except (IOError, OSError) as e:
                    self.logger.critical(_('Failed to remove transaction file %s') % fn)

        self.rpmdb.dropCachedData() # drop out the rpm cache so we don't step on bad hdr indexes
        self.plugins.run('posttrans')
        # sync up what just happened versus what is in the rpmdb
        if not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
            self.verifyTransaction(resultobject)
        return resultobject

    def verifyTransaction(self, resultobject=None):
        """checks that the transaction did what we expected it to do. Also 
           propagates our external yumdb info"""
        
        # check to see that the rpmdb and the tsInfo roughly matches
        # push package object metadata outside of rpmdb into yumdb
        # delete old yumdb metadata entries
        
        # for each pkg in the tsInfo
        # if it is an install - see that the pkg is installed
        # if it is a remove - see that the pkg is no longer installed, provided
        #    that there is not also an install of this pkg in the tsInfo (reinstall)
        # for any kind of install add from_repo to the yumdb, and the cmdline
        # and the install reason

        self.rpmdb.dropCachedData()
        self.plugins.run('preverifytrans')
        for txmbr in self.tsInfo:
            if txmbr.output_state in TS_INSTALL_STATES:
                if not self.rpmdb.contains(po=txmbr.po):
                    # maybe a file log here, too
                    # but raising an exception is not going to do any good
                    self.logger.critical(_('%s was supposed to be installed' \
                                           ' but is not!' % txmbr.po))
                    continue
                po = self.getInstalledPackageObject(txmbr.pkgtup)
                rpo = txmbr.po
                po.yumdb_info.from_repo = rpo.repoid
                po.yumdb_info.reason = txmbr.reason
                po.yumdb_info.releasever = self.conf.yumvar['releasever']
                if hasattr(self, 'args') and self.args:
                    po.yumdb_info.command_line = ' '.join(self.args)
                elif hasattr(self, 'cmds') and self.cmds:
                    po.yumdb_info.command_line = ' '.join(self.cmds)
                csum = rpo.returnIdSum()
                if csum is not None:
                    po.yumdb_info.checksum_type = str(csum[0])
                    po.yumdb_info.checksum_data = str(csum[1])

                if isinstance(rpo, YumLocalPackage):
                    try:
                        st = os.stat(rpo.localPkg())
                        lp_ctime = str(int(st.st_ctime))
                        lp_mtime = str(int(st.st_mtime))
                        po.yumdb_info.from_repo_revision  = lp_ctime
                        po.yumdb_info.from_repo_timestamp = lp_mtime
                    except: pass

                if not hasattr(rpo.repo, 'repoXML'):
                    continue

                md = rpo.repo.repoXML
                if md and md.revision is not None:
                    po.yumdb_info.from_repo_revision  = str(md.revision)
                if md:
                    po.yumdb_info.from_repo_timestamp = str(md.timestamp)

                loginuid = misc.getloginuid()
                if loginuid is None:
                    continue
                loginuid = str(loginuid)
                if txmbr.updates or txmbr.downgrades or txmbr.reinstall:
                    if txmbr.updates:
                        opo = txmbr.updates[0]
                    elif txmbr.downgrades:
                        opo = txmbr.downgrades[0]
                    else:
                        opo = po
                    if 'installed_by' in opo.yumdb_info:
                        po.yumdb_info.installed_by = opo.yumdb_info.installed_by
                    po.yumdb_info.changed_by = loginuid
                else:
                    po.yumdb_info.installed_by = loginuid

        # Remove old ones after installing new ones, so we can copy values.
        for txmbr in self.tsInfo:
            if txmbr.output_state in TS_INSTALL_STATES:
                pass
            elif txmbr.output_state in TS_REMOVE_STATES:
                if self.rpmdb.contains(po=txmbr.po):
                    if not self.tsInfo.getMembersWithState(pkgtup=txmbr.pkgtup,
                                output_states=TS_INSTALL_STATES):
                        # maybe a file log here, too
                        # but raising an exception is not going to do any good
                        self.logger.critical(_('%s was supposed to be removed' \
                                               ' but is not!' % txmbr.po))
                        continue
                yumdb_item = self.rpmdb.yumdb.get_package(po=txmbr.po)
                yumdb_item.clean()
            else:
                self.verbose_logger.log(logginglevels.DEBUG_2, 'What is this? %s' % txmbr.po)

        self.plugins.run('postverifytrans')
        if self.conf.history_record and not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
            ret = -1
            if resultobject is not None:
                ret = resultobject.return_code
            self.plugins.run('historyend')
            self.history.end(self.rpmdb.simpleVersion(main_only=True)[0], ret)
        self.rpmdb.dropCachedData()

    def costExcludePackages(self):
        """ Create an excluder for repos. with higher cost. Eg.
            repo-A:cost=1 repo-B:cost=2 ... here we setup an excluder on repo-B
            that looks for pkgs in repo-B."""
        
        # if all the repo.costs are equal then don't bother running things
        costs = {}
        for r in self.repos.listEnabled():
            costs.setdefault(r.cost, []).append(r)

        if len(costs) <= 1:
            return

        done = False
        exid = "yum.costexcludes"
        orepos = []
        for cost in sorted(costs):
            if done: # Skip the first one, as they have lowest cost so are good.
                for repo in costs[cost]:
                    yce = _YumCostExclude(repo, self.repos)
                    repo.sack.addPackageExcluder(repo.id, exid,
                                                 'exclude.pkgtup.in', yce)
            orepos.extend(costs[cost])
            done = True

    def excludePackages(self, repo=None):
        """removes packages from packageSacks based on global exclude lists,
           command line excludes and per-repository excludes, takes optional 
           repo object to use."""

        if "all" in self.conf.disable_excludes:
            return
        
        # if not repo: then assume global excludes, only
        # if repo: then do only that repos' packages and excludes
        
        if not repo: # global only
            if "main" in self.conf.disable_excludes:
                return
            excludelist = self.conf.exclude
            repoid = None
            exid_beg = 'yum.excludepkgs'
        else:
            if repo.id in self.conf.disable_excludes:
                return
            excludelist = repo.getExcludePkgList()
            repoid = repo.id
            exid_beg = 'yum.excludepkgs.' + repoid

        count = 0
        for match in excludelist:
            count += 1
            exid = "%s.%u" % (exid_beg, count)
            self.pkgSack.addPackageExcluder(repoid, exid,'exclude.match', match)

    def includePackages(self, repo):
        """removes packages from packageSacks based on list of packages, to include.
           takes repoid as a mandatory argument."""
        
        includelist = repo.getIncludePkgList()
        
        if len(includelist) == 0:
            return
        
        # includepkgs actually means "exclude everything that doesn't match".
        #  So we mark everything, then wash those we want to keep and then
        # exclude everything that is marked.
        exid = "yum.includepkgs.1"
        self.pkgSack.addPackageExcluder(repo.id, exid, 'mark.washed')
        count = 0
        for match in includelist:
            count += 1
            exid = "%s.%u" % ("yum.includepkgs.2", count)
            self.pkgSack.addPackageExcluder(repo.id, exid, 'wash.match', match)
        exid = "yum.includepkgs.3"
        self.pkgSack.addPackageExcluder(repo.id, exid, 'exclude.marked')
        
    def doLock(self, lockfile = YUM_PID_FILE):
        """perform the yum locking, raise yum-based exceptions, not OSErrors"""
        
        # if we're not root then we don't lock - just return nicely
        if self.conf.uid != 0:
            return
            
        root = self.conf.installroot
        lockfile = root + '/' + lockfile # lock in the chroot
        lockfile = os.path.normpath(lockfile) # get rid of silly preceding extra /
        
        mypid=str(os.getpid())    
        while not self._lock(lockfile, mypid, '0644'):#(lockfile, mypid, '0644')
            try:
                fd = open(lockfile, 'r')
            except (IOError, OSError) as e:
                msg = _("Could not open lock %s: %s") % (lockfile, e)
                raise Errors.LockError(1, msg)
                
            try: oldpid = int(fd.readline())
            except ValueError:
                # bogus data in the pid file. Throw away.
                self._unlock(lockfile)
            else:
                if oldpid == os.getpid(): # if we own the lock, we're fine
                    break
                try: os.kill(oldpid, 0)
                except OSError as e:
                    if e[0] == errno.ESRCH:
                        # The pid doesn't exist
                        self._unlock(lockfile)
                    else:
                        # Whoa. What the heck happened?
                        msg = _('Unable to check if PID %s is active') % oldpid
                        raise Errors.LockError(1, msg, oldpid)
                else:
                    # Another copy seems to be running.
                    msg = _('Existing lock %s: another copy is running as pid %s.') % (lockfile, oldpid)
                    raise Errors.LockError(0, msg, oldpid)
        # We've got the lock, store it so we can auto-unlock on __del__...
        self._lockfile = lockfile
    
    def doUnlock(self, lockfile=None):
        """do the unlock for yum"""
        
        # if we're not root then we don't lock - just return nicely
        #  Note that we can get here from __del__, so if we haven't created
        # YumBase.conf we don't want to do so here as creating stuff inside
        # __del__ is bad.
        if hasattr(self, 'preconf') or self.conf.uid != 0:
            return
        
        if lockfile is not None:
            root = self.conf.installroot
            lockfile = root + '/' + lockfile # lock in the chroot
        elif self._lockfile is None:
            return # Don't delete other people's lock files on __del__
        else:
            lockfile = self._lockfile # Get the value we locked with
        
        self._unlock(lockfile)
        self._lockfile = None
        
    def _lock(self, filename, contents='', mode='0777'):#mode=0777)
        lockdir = os.path.dirname(filename)
        try:
            if not os.path.exists(lockdir):
                os.makedirs(lockdir, mode='0755')#mode=0755)
            fd = os.open(filename, os.O_EXCL|os.O_CREAT|os.O_WRONLY, mode)    
        except OSError as msg:#or, msg
            if not msg.errno == errno.EEXIST: 
                # Whoa. What the heck happened?
                errmsg = _('Could not create lock at %s: %s ') % (filename, str(msg))
                raise Errors.LockError(msg.errno, errmsg, contents)
            return 0
        else:
            os.write(fd, contents)
            os.close(fd)
            return 1
    
    def _unlock(self, filename):
        misc.unlink_f(filename)

    def verifyPkg(self, fo, po, raiseError):
        """verifies the package is what we expect it to be
           raiseError  = defaults to 0 - if 1 then will raise
           a URLGrabError if the file does not check out.
           otherwise it returns false for a failure, true for success"""
        failed = False

        if type(fo) is types.InstanceType:
            fo = fo.filename
        
        if fo != po.localPkg():
            po.localpath = fo

        if not po.verifyLocalPkg():
            failed = True
        else:
            ylp = YumLocalPackage(self.rpmdb.readOnlyTS(), fo)
            if ylp.pkgtup != po.pkgtup:
                failed = True


        if failed:            
            # if the file is wrong AND it is >= what we expected then it
            # can't be redeemed. If we can, kill it and start over fresh
            cursize = os.stat(fo)[6]
            totsize = LONG(po.size)#long(po.size)
            if cursize >= totsize and not po.repo.cache:
                # if the path to the file is NOT inside the cachedir then don't
                # unlink it b/c it is probably a file:// url and possibly
                # unlinkable
                if fo.startswith(po.repo.cachedir):
                    os.unlink(fo)

            if raiseError:
                msg = _('Package does not match intended download. Suggestion: run yum --enablerepo=%s clean metadata') %  po.repo.id 
                raise URLGrabError(-1, msg)
            else:
                return False

        
        return True
        
        
    def verifyChecksum(self, fo, checksumType, csum):
        """Verify the checksum of the file versus the 
           provided checksum"""

        try:
            filesum = misc.checksum(checksumType, fo)
        except Errors.MiscError as e:
            raise URLGrabError(-3, _('Could not perform checksum'))
            
        if filesum != csum:
            raise URLGrabError(-1, _('Package does not match checksum'))
        
        return 0

    def downloadPkgs(self, pkglist, callback=None, callback_total=None):
        def mediasort(apo, bpo):
            # FIXME: we should probably also use the mediaid; else we
            # could conceivably ping-pong between different disc1's
            a = apo.getDiscNum()
            b = bpo.getDiscNum()
            if a is None and b is None:
                return cmp(apo, bpo)
            if a is None:
                return -1
            if b is None:
                return 1
            if a < b:
                return -1
            elif a > b:
                return 1
            return 0
        
        """download list of package objects handed to you, output based on
           callback, raise yum.Errors.YumBaseError on problems"""

        errors = {}
        def adderror(po, msg):
            errors.setdefault(po, []).append(msg)

        #  We close the history DB here because some plugins (presto) use
        # threads. And sqlite really doesn't like threads. And while I don't
        # think it should matter, we've had some reports of history DB
        # corruption, and it was implied that it happened just after C-c
        # at download time and this is a safe thing to do.
        #  Note that manual testing shows that history is not connected by
        # this point, from the cli with no plugins. So this really does
        # nothing *sigh*.
        self.history.close()

        self.plugins.run('predownload', pkglist=pkglist)
        repo_cached = False
        remote_pkgs = []
        remote_size = 0
        for po in pkglist:
            if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
                continue
                    
            local = po.localPkg()
            if os.path.exists(local):
                if not self.verifyPkg(local, po, False):
                    if po.repo.cache:
                        repo_cached = True
                        adderror(po, _('package fails checksum but caching is '
                            'enabled for %s') % po.repo.id)
                else:
                    self.verbose_logger.debug(_("using local copy of %s") %(po,))
                    continue
                        
            remote_pkgs.append(po)
            remote_size += po.size
            
            # caching is enabled and the package 
            # just failed to check out there's no 
            # way to save this, report the error and return
            if (self.conf.cache or repo_cached) and errors:
                return errors
                

        remote_pkgs.sort(mediasort)
        #  This is kind of a hack and does nothing in non-Fedora versions,
        # we'll fix it one way or anther soon.
        if (hasattr(urlgrabber.progress, 'text_meter_total_size') and
            len(remote_pkgs) > 1):
            urlgrabber.progress.text_meter_total_size(remote_size)
        beg_download = time.time()
        i = 0
        local_size = 0
        for po in remote_pkgs:
            #  Recheck if the file is there, works around a couple of weird
            # edge cases.
            local = po.localPkg()
            i += 1
            if os.path.exists(local):
                if self.verifyPkg(local, po, False):
                    self.verbose_logger.debug(_("using local copy of %s") %(po,))
                    remote_size -= po.size
                    if hasattr(urlgrabber.progress, 'text_meter_total_size'):
                        urlgrabber.progress.text_meter_total_size(remote_size,
                                                                  local_size)
                    continue
                if os.path.getsize(local) >= po.size:
                    os.unlink(local)

            checkfunc = (self.verifyPkg, (po, 1), {})
            dirstat = os.statvfs(po.repo.pkgdir)
            if (dirstat.f_bavail * dirstat.f_bsize) <= LONG(po.size):#long(po.size)
                adderror(po, _('Insufficient space in download directory %s\n'
                        "    * free   %s\n"
                        "    * needed %s") %
                         (po.repo.pkgdir,
                          format_number(dirstat.f_bavail * dirstat.f_bsize),
                          format_number(po.size)))
                continue
            
            try:
                if i == 1 and not local_size and remote_size == po.size:
                    text = os.path.basename(po.relativepath)
                else:
                    text = '(%s/%s): %s' % (i, len(remote_pkgs),
                                            os.path.basename(po.relativepath))
                mylocal = po.repo.getPackage(po,
                                   checkfunc=checkfunc,
                                   text=text,
                                   cache=po.repo.http_caching != 'none',
                                   )
                local_size += po.size
                if hasattr(urlgrabber.progress, 'text_meter_total_size'):
                    urlgrabber.progress.text_meter_total_size(remote_size,
                                                              local_size)
            except Errors.RepoError as e:
                adderror(po, str(e))
            else:
                po.localpath = mylocal
                if po in errors:
                    del errors[po]

        if hasattr(urlgrabber.progress, 'text_meter_total_size'):
            urlgrabber.progress.text_meter_total_size(0)
        if callback_total is not None and not errors:
            callback_total(remote_pkgs, remote_size, beg_download)

        self.plugins.run('postdownload', pkglist=pkglist, errors=errors)

        return errors

    def verifyHeader(self, fo, po, raiseError):
        """check the header out via it's naevr, internally"""
        if type(fo) is types.InstanceType:
            fo = fo.filename
            
        try:
            hlist = rpm.readHeaderListFromFile(fo)
            hdr = hlist[0]
        except (rpm.error, IndexError):
            if raiseError:
                raise URLGrabError(-1, _('Header is not complete.'))
            else:
                return 0
                
        yip = YumInstalledPackage(hdr) # we're using YumInstalledPackage b/c
                                       # it takes headers <shrug>
        if yip.pkgtup != po.pkgtup:
            if raiseError:
                raise URLGrabError(-1, 'Header does not match intended download')
            else:
                return 0
        
        return 1
        
    def downloadHeader(self, po):
        """download a header from a package object.
           output based on callback, raise yum.Errors.YumBaseError on problems"""

        if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            return
                
        errors = {}
        local =  po.localHdr()
        repo = self.repos.getRepo(po.repoid)
        if os.path.exists(local):
            try:
                result = self.verifyHeader(local, po, raiseError=1)
            except URLGrabError as e:
                # might add a check for length of file - if it is < 
                # required doing a reget
                misc.unlink_f(local)
            else:
                po.hdrpath = local
                return
        else:
            if self.conf.cache:
                raise Errors.RepoError(_('Header not in local cache and caching-only mode enabled. Cannot download %s') % po.hdrpath)#, \
                
        
        if self.dsCallback: self.dsCallback.downloadHeader(po.name)
        
        try:
            if not os.path.exists(repo.hdrdir):
                os.makedirs(repo.hdrdir)
            checkfunc = (self.verifyHeader, (po, 1), {})
            hdrpath = repo.getHeader(po, checkfunc=checkfunc,
                    cache=repo.http_caching != 'none',
                    )
        except Errors.RepoError as e:
            saved_repo_error = e
            try:
                misc.unlink_f(local)
            except OSError as e:
                raise Errors.RepoError(saved_repo_error)#, saved_repo_error
            else:
                raise Errors.RepoError(saved_repo_error)#, saved_repo_error
        else:
            po.hdrpath = hdrpath
            return

    def sigCheckPkg(self, po):
        '''
        Take a package object and attempt to verify GPG signature if required

        Returns (result, error_string) where result is:
            - 0 - GPG signature verifies ok or verification is not required.
            - 1 - GPG verification failed but installation of the right GPG key
                  might help.
            - 2 - Fatal GPG verification error, give up.
        '''
        if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            check = self.conf.gpgcheck
            hasgpgkey = 0
        else:
            repo = self.repos.getRepo(po.repoid)
            check = repo.gpgcheck
            hasgpgkey = not not repo.gpgkey 
        
        if check:
            ts = self.rpmdb.readOnlyTS()
            sigresult = rpmUtils.miscutils.checkSig(ts, po.localPkg())
            localfn = os.path.basename(po.localPkg())
            
            if sigresult == 0:
                result = 0
                msg = ''

            elif sigresult == 1:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                msg = _('Public key for %s is not installed') % localfn

            elif sigresult == 2:
                result = 2
                msg = _('Problem opening package %s') % localfn

            elif sigresult == 3:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                result = 1
                msg = _('Public key for %s is not trusted') % localfn

            elif sigresult == 4:
                result = 2 
                msg = _('Package %s is not signed') % localfn
            
        else:
            result =0
            msg = ''

        return result, msg

    def cleanUsedHeadersPackages(self):
        filelist = []
        for txmbr in self.tsInfo:
            if txmbr.po.state not in TS_INSTALL_STATES:
                continue
            if txmbr.po.repoid == "installed":
                continue
            if txmbr.po.repoid not in self.repos.repos:
                continue
            
            # make sure it's not a local file
            repo = self.repos.repos[txmbr.po.repoid]
            local = False
            for u in repo.baseurl:
                if u.startswith("file:"):
                    local = True
                    break
                
            if local:
                filelist.extend([txmbr.po.localHdr()])
            else:
                filelist.extend([txmbr.po.localPkg(), txmbr.po.localHdr()])

        # now remove them
        for fn in filelist:
            if not os.path.exists(fn):
                continue
            try:
                misc.unlink_f(fn)
            except OSError as e:
                self.logger.warning(_('Cannot remove %s'), fn)
                continue
            else:
                self.verbose_logger.log(logginglevels.DEBUG_4,
                    _('%s removed'), fn)
        
    def cleanHeaders(self):
        exts = ['hdr']
        return self._cleanFiles(exts, 'hdrdir', 'header')

    def cleanPackages(self):
        exts = ['rpm']
        return self._cleanFiles(exts, 'pkgdir', 'package')

    def cleanSqlite(self):
        exts = ['sqlite', 'sqlite.bz2']
        return self._cleanFiles(exts, 'cachedir', 'sqlite')

    def cleanMetadata(self):
        exts = ['xml.gz', 'xml', 'cachecookie', 'mirrorlist.txt', 'asc']
        # Metalink is also here, but is a *.xml file
        return self._cleanFiles(exts, 'cachedir', 'metadata') 

    def cleanExpireCache(self):
        exts = ['cachecookie', 'mirrorlist.txt']
        return self._cleanFiles(exts, 'cachedir', 'metadata')

    def cleanRpmDB(self):
        cachedir = self.conf.persistdir + "/rpmdb-indexes/"
        if not os.path.exists(cachedir):
            filelist = []
        else:
            filelist = misc.getFileList(cachedir, '', [])
        return self._cleanFilelist('rpmdb', filelist)

    def _cleanFiles(self, exts, pathattr, filetype):
        filelist = []
        for ext in exts:
            for repo in self.repos.listEnabled():
                path = getattr(repo, pathattr)
                if os.path.exists(path) and os.path.isdir(path):
                    filelist = misc.getFileList(path, ext, filelist)
        return self._cleanFilelist(filetype, filelist)

    def _cleanFilelist(self, filetype, filelist):
        removed = 0
        for item in filelist:
            try:
                misc.unlink_f(item)
            except OSError as e:
                self.logger.critical(_('Cannot remove %s file %s'), filetype, item)
                continue
            else:
                self.verbose_logger.log(logginglevels.DEBUG_4,
                    _('%s file %s removed'), filetype, item)
                removed+=1
        msg = _('%d %s files removed') % (removed, filetype)
        return 0, [msg]

    def doPackageLists(self, pkgnarrow='all', patterns=None, showdups=None,
                       ignore_case=False):
        """generates lists of packages, un-reduced, based on pkgnarrow option"""

        if showdups is None:
            showdups = self.conf.showdupesfromrepos
        ygh = misc.GenericHolder(iter=pkgnarrow)
        
        installed = []
        available = []
        reinstall_available = []
        old_available = []
        updates = []
        obsoletes = []
        obsoletesTuples = []
        recent = []
        extras = []

        ic = ignore_case
        # list all packages - those installed and available, don't 'think about it'
        if pkgnarrow == 'all': 
            dinst = {}
            ndinst = {} # Newest versions by name.arch
            for po in self.rpmdb.returnPackages(patterns=patterns,
                                                ignore_case=ic):
                dinst[po.pkgtup] = po
                if showdups:
                    continue
                key = (po.name, po.arch)
                if key not in ndinst or po.verGT(ndinst[key]):
                    ndinst[key] = po
            installed = dinst.values()
                        
            if showdups:
                avail = self.pkgSack.returnPackages(patterns=patterns,
                                                    ignore_case=ic)
            else:
                try:
                    avail = self.pkgSack.returnNewestByNameArch(patterns=patterns,
                                                              ignore_case=ic)
                except Errors.PackageSackError:
                    avail = []
            
            for pkg in avail:
                if showdups:
                    if pkg.pkgtup in dinst:
                        reinstall_available.append(pkg)
                    else:
                        available.append(pkg)
                else:
                    key = (pkg.name, pkg.arch)
                    if pkg.pkgtup in dinst:
                        reinstall_available.append(pkg)
                    elif key not in ndinst or pkg.verGT(ndinst[key]):
                        available.append(pkg)
                    else:
                        old_available.append(pkg)

        # produce the updates list of tuples
        elif pkgnarrow == 'updates':
            for (n,a,e,v,r) in self.up.getUpdatesList():
                matches = self.pkgSack.searchNevra(name=n, arch=a, epoch=e, 
                                                   ver=v, rel=r)
                if len(matches) > 1:
                    updates.append(matches[0])
                    self.verbose_logger.log(logginglevels.DEBUG_1,
                        _('More than one identical match in sack for %s'), 
                        matches[0])
                elif len(matches) == 1:
                    updates.append(matches[0])
                else:
                    self.verbose_logger.log(logginglevels.DEBUG_1,
                        _('Nothing matches %s.%s %s:%s-%s from update'), n,a,e,v,r)
            if patterns:
                exactmatch, matched, unmatched = \
                   parsePackages(updates, patterns, casematch=not ignore_case)
                updates = exactmatch + matched

        # installed only
        elif pkgnarrow == 'installed':
            installed = self.rpmdb.returnPackages(patterns=patterns,
                                                  ignore_case=ic)
        
        # available in a repository
        elif pkgnarrow == 'available':

            if showdups:
                avail = self.pkgSack.returnPackages(patterns=patterns,
                                                    ignore_case=ic)
            else:
                try:
                    avail = self.pkgSack.returnNewestByNameArch(patterns=patterns,
                                                              ignore_case=ic)
                except Errors.PackageSackError:
                    avail = []
            
            for pkg in avail:
                if showdups:
                    if self.rpmdb.contains(po=pkg):
                        reinstall_available.append(pkg)
                    else:
                        available.append(pkg)
                else:
                    ipkgs = self.rpmdb.searchNevra(pkg.name, arch=pkg.arch)
                    if ipkgs:
                        latest = sorted(ipkgs, reverse=True)[0]
                    if not ipkgs or pkg.verGT(latest):
                        available.append(pkg)
                    elif pkg.verEQ(latest):
                        reinstall_available.append(pkg)
                    else:
                        old_available.append(pkg)

        # not in a repo but installed
        elif pkgnarrow == 'extras':
            # we must compare the installed set versus the repo set
            # anything installed but not in a repo is an extra
            avail = self.pkgSack.simplePkgList(patterns=patterns,
                                               ignore_case=ic)
            avail = set(avail)
            for po in self.rpmdb.returnPackages(patterns=patterns,
                                                ignore_case=ic):
                if po.pkgtup not in avail:
                    extras.append(po)

        # obsoleting packages (and what they obsolete)
        elif pkgnarrow == 'obsoletes':
            self.conf.obsoletes = 1

            for (pkgtup, instTup) in self.up.getObsoletesTuples():
                (n,a,e,v,r) = pkgtup
                pkgs = self.pkgSack.searchNevra(name=n, arch=a, ver=v, rel=r, epoch=e)
                instpo = self.getInstalledPackageObject(instTup)
                for po in pkgs:
                    obsoletes.append(po)
                    obsoletesTuples.append((po, instpo))
            if patterns:
                exactmatch, matched, unmatched = \
                   parsePackages(obsoletes, patterns, casematch=not ignore_case)
                obsoletes = exactmatch + matched
                matched_obsoletes = set(obsoletes)
                nobsoletesTuples = []
                for po, instpo in obsoletesTuples:
                    if po not in matched_obsoletes:
                        continue
                    nobsoletesTuples.append((po, instpo))
                obsoletesTuples = nobsoletesTuples
            if not showdups:
                obsoletes = packagesNewestByName(obsoletes)
                filt = set(obsoletes)
                nobsoletesTuples = []
                for po, instpo in obsoletesTuples:
                    if po not in filt:
                        continue
                    nobsoletesTuples.append((po, instpo))
                obsoletesTuples = nobsoletesTuples
        
        # packages recently added to the repositories
        elif pkgnarrow == 'recent':
            now = time.time()
            recentlimit = now-(self.conf.recent*86400)
            if showdups:
                avail = self.pkgSack.returnPackages(patterns=patterns,
                                                    ignore_case=ic)
            else:
                try:
                    avail = self.pkgSack.returnNewestByNameArch(patterns=patterns,
                                                              ignore_case=ic)
                except Errors.PackageSackError:
                    avail = []
            
            for po in avail:
                if int(po.filetime) > recentlimit:
                    recent.append(po)
        
        
        ygh.installed = installed
        ygh.available = available
        ygh.reinstall_available = reinstall_available
        ygh.old_available = old_available
        ygh.updates = updates
        ygh.obsoletes = obsoletes
        ygh.obsoletesTuples = obsoletesTuples
        ygh.recent = recent
        ygh.extras = extras

        return ygh


        
    def findDeps(self, pkgs):
        """
        Return the dependencies for a given package object list, as well
        possible solutions for those dependencies.
           
        Returns the deps as a dict of dicts::
            packageobject = [reqs] = [list of satisfying pkgs]
        """
        
        results = {}

        for pkg in pkgs:
            results[pkg] = {} 
            reqs = pkg.requires
            reqs.sort()
            pkgresults = results[pkg] # shorthand so we don't have to do the
                                      # double bracket thing
            
            for req in reqs:
                (r,f,v) = req
                if r.startswith('rpmlib('):
                    continue
                
                satisfiers = []

                for po in self.whatProvides(r, f, v):
                    satisfiers.append(po)

                pkgresults[req] = satisfiers
        
        return results
    
    # pre 3.2.10 API used to always showdups, so that's the default atm.
    def searchGenerator(self, fields, criteria, showdups=True, keys=False):
        """Generator method to lighten memory load for some searches.
           This is the preferred search function to use. Setting keys to True
           will use the search keys that matched in the sorting, and return
           the search keys in the results. """
        sql_fields = []
        for f in fields:
            sql_fields.append(RPM_TO_SQLITE.get(f, f))

        # yield the results in order of most terms matched first
        sorted_lists = {} # count_of_matches = [(pkgobj, 
                          #                     [search strings which matched], 
                          #                     [results that matched])]
        tmpres = []
        real_crit = []
        real_crit_lower = [] # Take the s.lower()'s out of the loop
        rcl2c = {}
        # weigh terms in given order (earlier = more relevant)
        critweight = 0
        critweights = {}
        for s in criteria:
            real_crit.append(s)
            real_crit_lower.append(s.lower())
            rcl2c[s.lower()] = s
            critweights.setdefault(s, critweight)
            critweight -= 1

        for sack in self.pkgSack.sacks.values():
            tmpres.extend(sack.searchPrimaryFieldsMultipleStrings(sql_fields, real_crit))

        def results2sorted_lists(tmpres, sorted_lists):
            for (po, count) in tmpres:
                # check the pkg for sanity
                # pop it into the sorted lists
                tmpkeys   = set()
                tmpvalues = []
                if count not in sorted_lists: sorted_lists[count] = []
                for s in real_crit_lower:
                    for field in fields:
                        value = to_unicode(getattr(po, field))
                        if value and value.lower().find(s) != -1:
                            tmpvalues.append(value)
                            tmpkeys.add(rcl2c[s])

                if len(tmpvalues) > 0:
                    sorted_lists[count].append((po, tmpkeys, tmpvalues))
        results2sorted_lists(tmpres, sorted_lists)

        tmpres = self.rpmdb.searchPrimaryFieldsMultipleStrings(fields,
                                                               real_crit_lower,
                                                               lowered=True)
        # close our rpmdb connection so we can ctrl-c, kthxbai
        self.closeRpmDB()

        results2sorted_lists(tmpres, sorted_lists)
        del tmpres

        tmpres = self.searchPackageTags(real_crit_lower)
        
        results_by_pkg = {} # pkg=[list_of_tuples_of_values]
        
        for pkg in tmpres:
            count = 0
            matchkeys = []
            tagresults = []
            for (match, taglist) in tmpres[pkg]:
                count += len(taglist)
                matchkeys.append(rcl2c[match])
                tagresults.extend(taglist)
                if pkg not in results_by_pkg:
                    results_by_pkg[pkg] = []
                results_by_pkg[pkg].append((matchkeys, tagresults))

        del tmpres

        # do the ones we already have
        for item in sorted_lists.values():
            for pkg, k, v in item:
                if pkg not in results_by_pkg:
                    results_by_pkg[pkg] = []
                results_by_pkg[pkg].append((k,v))

        # take our existing dict-by-pkg and make the dict-by-count for 
        # this bizarro sorted_lists format
        # FIXME - stab sorted_lists in the chest at some later date
        sorted_lists = {}
        for pkg in results_by_pkg:
            totkeys = []
            totvals = []
            for (k, v) in results_by_pkg[pkg]:
                totkeys.extend(k)
                totvals.extend(v)
            
            totkeys = misc.unique(totkeys)
            totvals = misc.unique(totvals)
            count = len(totkeys)
            if count not in sorted_lists:
                sorted_lists[count] = []
            sorted_lists[count].append((pkg, totkeys, totvals))

        # By default just sort using package sorting
        sort_func = operator.itemgetter(0)
        if keys:
            # Take into account the keys found, their original order,
            # and number of fields hit as well
            sort_func = lambda x: (-sum((critweights[y] for y in x[1])),
                                   "\0".join(sorted(x[1])), -len(x[2]), x[0])
        yielded = {}
        for val in reversed(sorted(sorted_lists)):
            for (po, ks, vs) in sorted(sorted_lists[val], key=sort_func):
                if not showdups and (po.name, po.arch) in yielded:
                    continue

                if keys:
                    yield (po, ks, vs)
                else:
                    yield (po, vs)

                if not showdups:
                    yielded[(po.name, po.arch)] = 1

    def searchPackageTags(self, criteria):
        results = {} # name = [(criteria, taglist)]
        for c in criteria:
            c = c.lower()
            res = self.pkgtags.search_tags(c)
            for (name, taglist) in res.items():
                pkgs = self.pkgSack.searchNevra(name=name)
                if not pkgs:
                    continue
                pkg = pkgs[0]
                if pkg not in results:
                    results[pkg] = []
                results[pkg].append((c, taglist))
        
        return results
        
    def searchPackages(self, fields, criteria, callback=None):
        """Search specified fields for matches to criteria
           optional callback specified to print out results
           as you go. Callback is a simple function of:
           callback(po, matched values list). It will 
           just return a dict of dict[po]=matched values list"""
        warnings.warn(_('searchPackages() will go away in a future version of Yum.\
                      Use searchGenerator() instead. \n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)           
        matches = {}
        match_gen = self.searchGenerator(fields, criteria)
        
        for (po, matched_strings) in match_gen:
            if callback:
                callback(po, matched_strings)
            if po not in matches:
                matches[po] = []
            
            matches[po].extend(matched_strings)
        
        return matches
    
    def searchPackageProvides(self, args, callback=None,
                              callback_has_matchfor=False):
        
        matches = {}
        for arg in args:
            arg = to_unicode(arg)
            if not misc.re_glob(arg):
                isglob = False
                if arg[0] != '/':
                    canBeFile = False
                else:
                    canBeFile = True
            else:
                isglob = True
                canBeFile = misc.re_filename(arg)
                
            if not isglob:
                usedDepString = True
                where = self.returnPackagesByDep(arg)
            else:
                usedDepString = False
                where = self.pkgSack.searchAll(arg, False)
            self.verbose_logger.log(logginglevels.DEBUG_1,
                _('Searching %d packages'), len(where))
            
            for po in where:
                self.verbose_logger.log(logginglevels.DEBUG_2,
                    _('searching package %s'), po)
                tmpvalues = []
                
                if usedDepString:
                    tmpvalues.append(arg)

                if not isglob and canBeFile:
                    # then it is not a globbed file we have matched it precisely
                    tmpvalues.append(arg)
                    
                if isglob and canBeFile:
                    self.verbose_logger.log(logginglevels.DEBUG_2,
                        _('searching in file entries'))
                    for thisfile in po.dirlist + po.filelist + po.ghostlist:
                        if fnmatch.fnmatch(thisfile, arg):
                            tmpvalues.append(thisfile)
                

                self.verbose_logger.log(logginglevels.DEBUG_2,
                    _('searching in provides entries'))
                for (p_name, p_flag, (p_e, p_v, p_r)) in po.provides:
                    prov = misc.prco_tuple_to_string((p_name, p_flag, (p_e, p_v, p_r)))
                    if not usedDepString:
                        if fnmatch.fnmatch(p_name, arg) or fnmatch.fnmatch(prov, arg):
                            tmpvalues.append(prov)

                if len(tmpvalues) > 0:
                    if callback: # No matchfor, on globs
                        if not isglob and callback_has_matchfor:
                            callback(po, tmpvalues, args)
                        else:
                            callback(po, tmpvalues)
                    matches[po] = tmpvalues
        
        # installed rpms, too
        taglist = ['filelist', 'dirnames', 'provides_names']
        for arg in args:
            if not misc.re_glob(arg):
                isglob = False
                if arg[0] != '/':
                    canBeFile = False
                else:
                    canBeFile = True
            else:
                isglob = True
                canBeFile = True
            
            if not isglob:
                where = self.returnInstalledPackagesByDep(arg)
                usedDepString = True
                for po in where:
                    tmpvalues = []
                    msg = _('Provides-match: %s') % to_unicode(arg)
                    tmpvalues.append(msg)

                    if len(tmpvalues) > 0:
                        if callback:
                            if callback_has_matchfor:
                                callback(po, tmpvalues, args)
                            else:
                                callback(po, tmpvalues)
                        matches[po] = tmpvalues

            else:
                usedDepString = False
                where = self.rpmdb
                
                for po in where:
                    searchlist = []
                    tmpvalues = []
                    for tag in taglist:
                        tagdata = getattr(po, tag)
                        if tagdata is None:
                            continue
                        if type(tagdata) is types.ListType:
                            searchlist.extend(tagdata)
                        else:
                            searchlist.append(tagdata)
                    
                    for item in searchlist:
                        if fnmatch.fnmatch(item, arg):
                            tmpvalues.append(item)
                
                    if len(tmpvalues) > 0:
                        if callback: # No matchfor, on globs
                            callback(po, tmpvalues)
                        matches[po] = tmpvalues
            
            
        return matches

    def doGroupLists(self, uservisible=0, patterns=None, ignore_case=True):
        """returns two lists of groups, installed groups and available groups
           optional 'uservisible' bool to tell it whether or not to return
           only groups marked as uservisible"""
        
        
        installed = []
        available = []

        if self.comps.compscount == 0:
            raise Errors.GroupsError(_('No group data available for configured repositories'))#, _('No group data available for configured repositories')
        
        if patterns is None:
            grps = self.comps.groups
        else:
            grps = self.comps.return_groups(",".join(patterns),
                                            case_sensitive=not ignore_case)
        for grp in grps:
            if grp.installed:
                if uservisible:
                    if grp.user_visible:
                        installed.append(grp)
                else:
                    installed.append(grp)
            else:
                if uservisible:
                    if grp.user_visible:
                        available.append(grp)
                else:
                    available.append(grp)
            
        return sorted(installed), sorted(available)
    
    
    def groupRemove(self, grpid):
        """mark all the packages in this group to be removed"""
        
        txmbrs_used = []
        
        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError(_("No Group named %s exists") % grpid)#, _("No Group named %s exists") % grpid

        for thisgroup in thesegroups:
            thisgroup.toremove = True
            pkgs = thisgroup.packages
            for pkg in thisgroup.packages:
                txmbrs = self.remove(name=pkg, silence_warnings=True)
                txmbrs_used.extend(txmbrs)
                for txmbr in txmbrs:
                    txmbr.groups.append(thisgroup.groupid)
            
        return txmbrs_used

    def groupUnremove(self, grpid):
        """unmark any packages in the group from being removed"""
        

        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError(_("No Group named %s exists") % grpid)#, _("No Group named %s exists") % grpid

        for thisgroup in thesegroups:
            thisgroup.toremove = False
            pkgs = thisgroup.packages
            for pkg in thisgroup.packages:
                for txmbr in self.tsInfo:
                    if txmbr.po.name == pkg and txmbr.po.state in TS_INSTALL_STATES:
                        try:
                            txmbr.groups.remove(grpid)
                        except ValueError:
                            self.verbose_logger.log(logginglevels.DEBUG_1,
                               _("package %s was not marked in group %s"), txmbr.po,
                                grpid)
                            continue
                        
                        # if there aren't any other groups mentioned then remove the pkg
                        if len(txmbr.groups) == 0:
                            self.tsInfo.remove(txmbr.po.pkgtup)
        
        
    def selectGroup(self, grpid, group_package_types=[], enable_group_conditionals=None):
        """mark all the packages in the group to be installed
           returns a list of transaction members it added to the transaction 
           set
           Optionally take:
           group_package_types=List - overrides self.conf.group_package_types
           enable_group_conditionals=Bool - overrides self.conf.enable_group_conditionals
        """

        if not self.comps.has_group(grpid):
            raise Errors.GroupsError(_("No Group named %s exists") % grpid)#, _("No Group named %s exists") % grpid
        
        txmbrs_used = []
        thesegroups = self.comps.return_groups(grpid)
     
        if not thesegroups:
            raise Errors.GroupsError(_("No Group named %s exists") % grpid)#, _("No Group named %s exists") % grpid

        package_types = self.conf.group_package_types
        if group_package_types:
            package_types = group_package_types

        for thisgroup in thesegroups:
            if thisgroup.selected:
                continue
            
            thisgroup.selected = True
            
            pkgs = []
            if 'mandatory' in package_types:
                pkgs.extend(thisgroup.mandatory_packages)
            if 'default' in package_types:
                pkgs.extend(thisgroup.default_packages)
            if 'optional' in package_types:
                pkgs.extend(thisgroup.optional_packages)

            for pkg in pkgs:
                self.verbose_logger.log(logginglevels.DEBUG_2,
                    _('Adding package %s from group %s'), pkg, thisgroup.groupid)
                try:
                    txmbrs = self.install(name = pkg)
                except Errors.InstallError as e:
                    self.verbose_logger.debug(_('No package named %s available to be installed'),
                        pkg)
                else:
                    txmbrs_used.extend(txmbrs)
                    for txmbr in txmbrs:
                        txmbr.groups.append(thisgroup.groupid)
            
            group_conditionals = self.conf.enable_group_conditionals
            if enable_group_conditionals is not None: # has to be this way so we can set it to False
                group_conditionals = enable_group_conditionals

            if group_conditionals:
                for condreq, cond in thisgroup.conditional_packages.iteritems():
                    if self.isPackageInstalled(cond):
                        try:
                            txmbrs = self.install(name = condreq)
                        except Errors.InstallError:
                            # we don't care if the package doesn't exist
                            continue
                        else:
                            if cond not in self.tsInfo.conditionals:
                                self.tsInfo.conditionals[cond]=[]

                        txmbrs_used.extend(txmbrs)
                        for txmbr in txmbrs:
                            txmbr.groups.append(thisgroup.groupid)
                            self.tsInfo.conditionals[cond].append(txmbr.po)
                        continue
                    # Otherwise we hook into tsInfo.add to make sure
                    # we'll catch it if it's added later in this transaction
                    pkgs = self.pkgSack.searchNevra(name=condreq)
                    if pkgs:
                        if self.arch.multilib:
                            if self.conf.multilib_policy == 'best':
                                use = []
                                best = self.arch.legit_multi_arches
                                best.append('noarch')
                                for pkg in pkgs:
                                    if pkg.arch in best:
                                        use.append(pkg)
                                pkgs = use
                               
                        pkgs = packagesNewestByName(pkgs)

                        if cond not in self.tsInfo.conditionals:
                            self.tsInfo.conditionals[cond] = []
                        self.tsInfo.conditionals[cond].extend(pkgs)
        return txmbrs_used

    def deselectGroup(self, grpid, force=False):
        """ Without the force option set, this removes packages from being
            installed that were added as part of installing one of the
            group(s). If the force option is set, then all installing packages
            in the group(s) are force removed from the transaction. """
        
        if not self.comps.has_group(grpid):
            raise Errors.GroupsError(_("No Group named %s exists") % grpid)#, _("No Group named %s exists") % grpid
            
        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError(_("No Group named %s exists") % grpid)#, _("No Group named %s exists") % grpid
        
        for thisgroup in thesegroups:
            thisgroup.selected = False
            
            for pkgname in thisgroup.packages:
                txmbrs = self.tsInfo.getMembersWithState(None,TS_INSTALL_STATES)
                for txmbr in txmbrs:
                    if txmbr.po.name != pkgname:
                        continue

                    if not force:
                        try: 
                            txmbr.groups.remove(grpid)
                        except ValueError:
                            self.verbose_logger.log(logginglevels.DEBUG_1,
                               _("package %s was not marked in group %s"), txmbr.po,
                                grpid)
                            continue
                        
                    # If the pkg isn't part of any group, or the group is
                    # being forced out ... then remove the pkg
                    if force or len(txmbr.groups) == 0:
                        self.tsInfo.remove(txmbr.po.pkgtup)
                        for pkg in self.tsInfo.conditionals.get(txmbr.name, []):
                            self.tsInfo.remove(pkg.pkgtup)
        
    def getPackageObject(self, pkgtup, allow_missing=False):
        """retrieves a packageObject from a pkgtuple - if we need
           to pick and choose which one is best we better call out
           to some method from here to pick the best pkgobj if there are
           more than one response - right now it's more rudimentary."""
           
        
        # look it up in the self.localPackages first:
        for po in self.localPackages:
            if po.pkgtup == pkgtup:
                return po
                
        pkgs = self.pkgSack.searchPkgTuple(pkgtup)

        if len(pkgs) == 0:
            if allow_missing: #  This can happen due to excludes after .up has
                return None   # happened.
            raise Errors.DepError(_('Package tuple %s could not be found in packagesack') % str(pkgtup))#, _('Package tuple %s could not be found in packagesack') % str(pkgtup)
            
        if len(pkgs) > 1: # boy it'd be nice to do something smarter here FIXME
            result = pkgs[0]
        else:
            result = pkgs[0] # which should be the only
        
            # this is where we could do something to figure out which repository
            # is the best one to pull from
        
        return result

    def getInstalledPackageObject(self, pkgtup):
        """ Returns a YumInstalledPackage object for the pkgtup specified, or
            raises an exception. You should use this instead of
            searchPkgTuple() if you are assuming there is a value. """

        pkgs = self.rpmdb.searchPkgTuple(pkgtup)
        if len(pkgs) == 0:
            raise Errors.RpmDBError(_('Package tuple %s could not be found in rpmdb') % str(pkgtup))#, _('Package tuple %s could not be found in rpmdb') % str(pkgtup)

        # Dito. FIXME from getPackageObject() for len() > 1 ... :)
        po = pkgs[0] # take the first one
        return po
        
    def gpgKeyCheck(self):
        """checks for the presence of gpg keys in the rpmdb
           returns 0 if no keys returns 1 if keys"""

        gpgkeyschecked = self.conf.cachedir + '/.gpgkeyschecked.yum'
        if os.path.exists(gpgkeyschecked):
            return 1
            
        myts = rpmUtils.transaction.initReadOnlyTransaction(root=self.conf.installroot)
        myts.pushVSFlags(~(rpm._RPMVSF_NOSIGNATURES|rpm._RPMVSF_NODIGESTS))
        idx = myts.dbMatch('name', 'gpg-pubkey')
        keys = idx.count()
        del idx
        del myts
        
        if keys == 0:
            return 0
        else:
            mydir = os.path.dirname(gpgkeyschecked)
            if not os.path.exists(mydir):
                os.makedirs(mydir)
                
            fo = open(gpgkeyschecked, 'w')
            fo.close()
            del fo
            return 1

    def returnPackagesByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the packages it finds providing that dep."""

        if not depstring:
            return []
        results = self.pkgSack.searchProvides(depstring)
        return results
        

    def returnPackageByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the best(or first) package it finds providing that dep."""
        
        # we get all sorts of randomness here
        errstring = depstring
        if type(depstring) not in types.StringTypes:
            errstring = str(depstring)
        
        try:
            pkglist = self.returnPackagesByDep(depstring)
        except Errors.YumBaseError:
            raise Errors.YumBaseError(_('No Package found for %s') % errstring)#, _('No Package found for %s') % errstring
        
        ps = ListPackageSack(pkglist)
        result = self._bestPackageFromList(ps.returnNewestByNameArch())
        if result is None:
            raise Errors.YumBaseError(_('No Package found for %s') % errstring)#, _('No Package found for %s') % errstring
        
        return result

    def returnInstalledPackagesByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the installed packages it finds providing that dep."""
        
        if not depstring:
            return []

        # parse the string out
        #  either it is 'dep (some operator) e:v-r'
        #  or /file/dep
        #  or packagename
        if type(depstring) == types.TupleType:
            (depname, depflags, depver) = depstring
        else:
            depname = depstring
            depflags = None
            depver = None
            
            if depstring[0] != '/':
                # not a file dep - look at it for being versioned
                dep_split = depstring.split()
                if len(dep_split) == 3:
                    depname, flagsymbol, depver = dep_split
                    if not flagsymbol in SYMBOLFLAGS:
                        raise Errors.YumBaseError(_('Invalid version flag'))#, _('Invalid version flag')
                    depflags = SYMBOLFLAGS[flagsymbol]

        return self.rpmdb.getProvides(depname, depflags, depver).keys()

    def _bestPackageFromList(self, pkglist):
        """take list of package objects and return the best package object.
           If the list is empty, return None. 
           
           Note: this is not aware of multilib so make sure you're only
           passing it packages of a single arch group."""
        
        
        if len(pkglist) == 0:
            return None
            
        if len(pkglist) == 1:
            return pkglist[0]

        bestlist = self._compare_providers(pkglist, None)
        return bestlist[0][0]

    def bestPackagesFromList(self, pkglist, arch=None, single_name=False):
        """Takes a list of packages, returns the best packages.
           This function is multilib aware so that it will not compare
           multilib to singlelib packages""" 
    
        returnlist = []
        compatArchList = self.arch.get_arch_list(arch)
        multiLib = []
        singleLib = []
        noarch = []
        for po in pkglist:
            if po.arch not in compatArchList:
                continue
            elif po.arch in ("noarch"):
                noarch.append(po)
            elif isMultiLibArch(arch=po.arch):
                multiLib.append(po)
            else:
                singleLib.append(po)
                
        # we now have three lists.  find the best package(s) of each
        multi = self._bestPackageFromList(multiLib)
        single = self._bestPackageFromList(singleLib)
        no = self._bestPackageFromList(noarch)

        if single_name and multi and single and multi.name != single.name:
            # Sinlge _must_ match multi, if we want a single package name
            single = None

        # now, to figure out which arches we actually want
        # if there aren't noarch packages, it's easy. multi + single
        if no is None:
            if multi: returnlist.append(multi)
            if single: returnlist.append(single)
        # if there's a noarch and it's newer than the multilib, we want
        # just the noarch.  otherwise, we want multi + single
        elif multi:
            best = self._bestPackageFromList([multi,no])
            if best.arch == "noarch":
                returnlist.append(no)
            else:
                if multi: returnlist.append(multi)
                if single: returnlist.append(single)
        # similar for the non-multilib case
        elif single:
            best = self._bestPackageFromList([single,no])
            if best.arch == "noarch":
                returnlist.append(no)
            else:
                returnlist.append(single)
        # if there's not a multi or single lib, then we want the noarch
        else:
            returnlist.append(no)

        return returnlist

    # FIXME: This doesn't really work, as it assumes one obsoleter for each pkg
    # when we can have:
    # 1 pkg obsoleted by multiple pkgs _and_
    # 1 pkg obsoleting multiple pkgs
    # ...and we need to detect loops, and get the arches "right" and do this
    # for chains. Atm. I hate obsoletes, and I can't get it to work better,
    # easily ... so screw it, don't create huge chains of obsoletes with some
    # loops in there too ... or I'll have to hurt you.
    def _pkg2obspkg(self, po):
        """ Given a package return the package it's obsoleted by and so
            we should install instead. Or None if there isn't one. """
        thispkgobsdict = self.up.checkForObsolete([po.pkgtup])
        if po.pkgtup in thispkgobsdict:
            obsoleting  = thispkgobsdict[po.pkgtup]
            oobsoleting = []
            # We want to keep the arch. of the obsoleted pkg. if possible.
            for opkgtup in obsoleting:
                if not canCoinstall(po.arch, opkgtup[1]):
                    oobsoleting.append(opkgtup)
            if oobsoleting:
                obsoleting = oobsoleting
            if len(obsoleting) > 1:
                # Pick the first name, and run with it...
                first = obsoleting[0]
                obsoleting = [pkgtup for pkgtup in obsoleting
                              if first[0] == pkgtup[0]]
            if len(obsoleting) > 1:
                # Lock to the latest version...
                def _sort_ver(x, y):
                    n1,a1,e1,v1,r1 = x
                    n2,a2,e2,v2,r2 = y
                    return compareEVR((e1,v1,r1), (e2,v2,r2))
                obsoleting.sort(_sort_ver)
                first = obsoleting[0]
                obsoleting = [pkgtup for pkgtup in obsoleting
                              if not _sort_ver(first, pkgtup)]
            if len(obsoleting) > 1:
                # Now do arch distance (see depsolve:compare_providers)...
                def _sort_arch_i(carch, a1, a2):
                    res1 = archDifference(carch, a1)
                    if not res1:
                        return 0
                    res2 = archDifference(carch, a2)
                    if not res2:
                        return 0
                    return res1 - res2
                def _sort_arch(x, y):
                    n1,a1,e1,v1,r1 = x
                    n2,a2,e2,v2,r2 = y
                    ret = _sort_arch_i(po.arch,            a1, a2)
                    if ret:
                        return ret
                    ret = _sort_arch_i(self.arch.bestarch, a1, a2)
                    return ret
                obsoleting.sort(_sort_arch)
            for pkgtup in obsoleting:
                pkg = self.getPackageObject(pkgtup, allow_missing=True)
                if pkg is not None:
                    return pkg
            return None
        return None

    def _test_loop(self, node, next_func):
        """ Generic comp. sci. test for looping, walk the list with two pointers
            moving one twice as fast as the other. If they are ever == you have
            a loop. If loop we return None, if no loop the last element. """
        slow = node
        done = False
        while True:
            next = next_func(node)
            if next is None and not done: return None
            if next is None: return node
            node = next_func(next)
            if node is None: return next
            done = True

            slow = next_func(slow)
            if next == slow:
                return None

    def _at_groupinstall(self, pattern):
        " Do groupinstall via. leading @ on the cmd line, for install/update."
        assert pattern[0] == '@'
        group_string = pattern[1:]
        tx_return = []
        for group in self.comps.return_groups(group_string):
            try:
                txmbrs = self.selectGroup(group.groupid)
                tx_return.extend(txmbrs)
            except yum.Errors.GroupsError:
                self.logger.critical(_('Warning: Group %s does not exist.'), group_string)
                continue
        return tx_return
        
    def _at_groupremove(self, pattern):
        " Do groupremove via. leading @ on the cmd line, for remove."
        assert pattern[0] == '@'
        group_string = pattern[1:]
        tx_return = []
        try:
            txmbrs = self.groupRemove(group_string)
        except yum.Errors.GroupsError:
            self.logger.critical(_('No group named %s exists'), group_string)
        else:
            tx_return.extend(txmbrs)
        return tx_return

    #  Note that this returns available pkgs, and not txmbrs like the other
    # _at_group* functions.
    def _at_groupdowngrade(self, pattern):
        " Do downgrade of a group via. leading @ on the cmd line."
        assert pattern[0] == '@'
        grpid = pattern[1:]

        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError(_("No Group named %s exists") % grpid)#, _("No Group named %s exists") % grpid
        pkgnames = set()
        for thisgroup in thesegroups:
            pkgnames.update(thisgroup.packages)
        return self.pkgSack.searchNames(pkgnames)

    def _minus_deselect(self, pattern):
        """ Remove things from the transaction, like kickstart. """
        assert pattern[0] == '-'
        pat = pattern[1:]

        if pat and pat[0] == '@':
            pat = pat[1:]
            return self.deselectGroup(pat)

        return self.tsInfo.deselect(pat)

    def _find_obsoletees(self, po):
        """ Return the pkgs. that are obsoleted by the po we pass in. """
        if not isinstance(po, YumLocalPackage):
            for (obstup, inst_tup) in self.up.getObsoletersTuples(name=po.name):
                if po.pkgtup == obstup:
                    installed_pkg =  self.getInstalledPackageObject(inst_tup)
                    yield installed_pkg
        else:
            for obs_n in po.obsoletes_names:
                for pkg in self.rpmdb.searchNevra(name=obs_n):
                    if pkg.obsoletedBy([po]):
                        yield pkg

    def _add_prob_flags(self, *flags):
        """ Add all of the passed flags to the tsInfo.probFilterFlags array. """
        for flag in flags:
            if flag not in self.tsInfo.probFilterFlags:
                self.tsInfo.probFilterFlags.append(flag)

    def install(self, po=None, **kwargs):
        """try to mark for install the item specified. Uses provided package 
           object, if available. If not it uses the kwargs and gets the best
           packages from the keyword options provided 
           returns the list of txmbr of the items it installs
           
           """
        
        pkgs = []
        was_pattern = False
        if po:
            if isinstance(po, YumAvailablePackage) or isinstance(po, YumLocalPackage):
                pkgs.append(po)
            else:
                raise Errors.InstallError(_('Package Object was not a package object instance'))#, _('Package Object was not a package object instance')
            
        else:
            if not kwargs:
                raise Errors.InstallError(_('Nothing specified to install'))#, _('Nothing specified to install')

            if 'pattern' in kwargs:
                if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                    return self._minus_deselect(kwargs['pattern'])

                if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                    return self._at_groupinstall(kwargs['pattern'])

                was_pattern = True
                pats = [kwargs['pattern']]
                mypkgs = self.pkgSack.returnPackages(patterns=pats,
                                                      ignore_case=False)
                pkgs.extend(mypkgs)
                # if we have anything left unmatched, let's take a look for it
                # being a dep like glibc.so.2 or /foo/bar/baz
                
                if not mypkgs:
                    arg = kwargs['pattern']
                    self.verbose_logger.debug(_('Checking for virtual provide or file-provide for %s'), 
                        arg)

                    try:
                        mypkgs = self.returnPackagesByDep(arg)
                    except yum.Errors.YumBaseError as e:
                        self.logger.critical(_('No Match for argument: %s') % arg)
                    else:
                        # install MTA* == fail, because provides don't do globs
                        # install /usr/kerberos/bin/* == success (and we want
                        #                                all of the pkgs)
                        if mypkgs and not misc.re_glob(arg):
                            mypkgs = self.bestPackagesFromList(mypkgs,
                                                               single_name=True)
                        if mypkgs:
                            pkgs.extend(mypkgs)
                        
            else:
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                pkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                     epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                     ver=nevra_dict['version'], rel=nevra_dict['release'])
                
            if pkgs:
                # if was_pattern or nevra-dict['arch'] is none, take the list
                # of arches based on our multilib_compat config and 
                # toss out any pkgs of any arch NOT in that arch list

                
                # only do these things if we're multilib
                if self.arch.multilib:
                    if was_pattern or not nevra_dict['arch']: # and only if they
                                                              # they didn't specify an arch
                        if self.conf.multilib_policy == 'best':
                            pkgs_by_name = {}
                            use = []
                            not_added = []
                            best = self.arch.legit_multi_arches
                            best.append('noarch')
                            for pkg in pkgs:
                                if pkg.arch in best:
                                    pkgs_by_name[pkg.name] = 1    
                                    use.append(pkg)  
                                else:
                                    not_added.append(pkg)
                            for pkg in not_added:
                                if not pkg.name in pkgs_by_name:
                                    use.append(pkg)
                           
                            pkgs = use
                           
                pkgs = packagesNewestByName(pkgs)

                pkgbyname = {}
                for pkg in pkgs:
                    if pkg.name not in pkgbyname:
                        pkgbyname[pkg.name] = [ pkg ]
                    else:
                        pkgbyname[pkg.name].append(pkg)

                lst = []
                for pkgs in pkgbyname.values():
                    lst.extend(self.bestPackagesFromList(pkgs))
                pkgs = lst


        if not pkgs:
            # Do we still want to return errors here?
            # We don't in the cases below, so I didn't here...
            if 'pattern' in kwargs:
                pkgs = self.rpmdb.returnPackages(patterns=[kwargs['pattern']],
                                                 ignore_case=False)
            if 'name' in kwargs:
                pkgs = self.rpmdb.searchNevra(name=kwargs['name'])
            if 'pkgtup' in kwargs:
                pkgs = self.rpmdb.searchNevra(name=kwargs['pkgtup'][0])
            # Warning here does "weird" things when doing:
            # yum --disablerepo='*' install '*'
            # etc. ... see RHBZ#480402
            if False:
                for pkg in pkgs:
                    self.verbose_logger.warning(_('Package %s installed and not available'), pkg)
            if pkgs:
                return []
            raise Errors.InstallError(_('No package(s) available to install'))#, _('No package(s) available to install')
        
        # FIXME - lots more checking here
        #  - install instead of erase
        #  - better error handling/reporting


        tx_return = []
        for po in pkgs:
            if self.tsInfo.exists(pkgtup=po.pkgtup):
                if self.tsInfo.getMembersWithState(po.pkgtup, TS_INSTALL_STATES):
                    self.verbose_logger.log(logginglevels.DEBUG_1,
                        _('Package: %s  - already in transaction set'), po)
                    tx_return.extend(self.tsInfo.getMembers(pkgtup=po.pkgtup))
                    continue
            
            # make sure this shouldn't be passed to update:
            if po.pkgtup in self.up.updating_dict:
                txmbrs = self.update(po=po)
                tx_return.extend(txmbrs)
                continue
            
            #  Make sure we're not installing a package which is obsoleted by
            # something else in the repo. Unless there is a obsoletion loop,
            # at which point ignore everything.
            obsoleting_pkg = self._test_loop(po, self._pkg2obspkg)
            if obsoleting_pkg is not None:
                # this is not a definitive check but it'll make sure we don't
                # pull in foo.i586 when foo.x86_64 already obsoletes the pkg and
                # is already installed
                already_obs = None
                pkgs = self.rpmdb.searchNevra(name=obsoleting_pkg.name)
                pkgs = po.obsoletedBy(pkgs, limit=1)
                if pkgs:
                    already_obs = pkgs[0]
                    continue

                if already_obs:
                    self.verbose_logger.warning(_('Package %s is obsoleted by %s which is already installed'), 
                                                po, already_obs)
                else:
                    if 'provides_for' in kwargs:
                        if not obsoleting_pkg.provides_for(kwargs['provides_for']):
                            self.verbose_logger.warning(_('Package %s is obsoleted by %s, but obsoleting package does not provide for requirements'),
                                                  po.name, obsoleting_pkg.name)
                            continue
                    self.verbose_logger.warning(_('Package %s is obsoleted by %s, trying to install %s instead'),
                        po.name, obsoleting_pkg.name, obsoleting_pkg)
                    tx_return.extend(self.install(po=obsoleting_pkg))
                continue
            
            # make sure it's not already installed
            if self.rpmdb.contains(po=po):
                if not self.tsInfo.getMembersWithState(po.pkgtup, TS_REMOVE_STATES):
                    self.verbose_logger.warning(_('Package %s already installed and latest version'), po)
                    continue

            # make sure we don't have a name.arch of this already installed
            # if so pass it to update b/c it should be able to figure it out
            # if self.rpmdb.contains(name=po.name, arch=po.arch) and not self.allowedMultipleInstalls(po):
            if not self.allowedMultipleInstalls(po):
                found = True
                for ipkg in self.rpmdb.searchNevra(name=po.name, arch=po.arch):
                    found = False
                    if self.tsInfo.getMembersWithState(ipkg.pkgtup, TS_REMOVE_STATES):
                        found = True
                        break
                if not found:
                    self.verbose_logger.warning(_('Package matching %s already installed. Checking for update.'), po)            
                    txmbrs = self.update(po=po)
                    tx_return.extend(txmbrs)
                    continue

                
            # at this point we are going to mark the pkg to be installed, make sure
            # it's not an older package that is allowed in due to multiple installs
            # or some other oddity. If it is - then modify the problem filter to cope
            
            for ipkg in self.rpmdb.searchNevra(name=po.name, arch=po.arch):
                if ipkg.verEQ(po):
                    self._add_prob_flags(rpm.RPMPROB_FILTER_REPLACEPKG,
                                         rpm.RPMPROB_FILTER_REPLACENEWFILES,
                                         rpm.RPMPROB_FILTER_REPLACEOLDFILES)
                    #  Yum needs the remove to happen before we allow the
                    # install of the same version. But rpm doesn't like that
                    # as it then has an install which removes the old version
                    # and a remove, which also tries to remove the old version.
                    self.tsInfo.remove(ipkg.pkgtup)
                    break
            for ipkg in self.rpmdb.searchNevra(name=po.name):
                if ipkg.verGT(po) and not canCoinstall(ipkg.arch, po.arch):
                    self._add_prob_flags(rpm.RPMPROB_FILTER_OLDPACKAGE)
                    break
            
            # it doesn't obsolete anything. If it does, mark that in the tsInfo, too
            if po.pkgtup in self.up.getObsoletesList(name=po.name):
                for obsoletee in self._find_obsoletees(po):
                    txmbr = self.tsInfo.addObsoleting(po, obsoletee)
                    self.tsInfo.addObsoleted(obsoletee, po)
                    tx_return.append(txmbr)
            else:
                txmbr = self.tsInfo.addInstall(po)
                tx_return.append(txmbr)

        return tx_return

    def _check_new_update_provides(self, opkg, npkg):
        """ Check for any difference in the provides of the old and new update
            that is needed by the transaction. If so we "update" those pkgs
            too, to the latest version. """
        oprovs = set(opkg.returnPrco('provides'))
        nprovs = set(npkg.returnPrco('provides'))
        tx_return = []
        for prov in oprovs.difference(nprovs):
            reqs = self.tsInfo.getRequires(*prov)
            for pkg in reqs:
                for req in reqs[pkg]:
                    if not npkg.inPrcoRange('provides', req):
                        naTup = (pkg.name, pkg.arch)
                        for pkg in self.pkgSack.returnNewestByNameArch(naTup):
                            tx_return.extend(self.update(po=pkg))
                        break
        return tx_return

    def _newer_update_in_trans(self, pkgtup, available_pkg, tx_return):
        """ We return True if there is a newer package already in the
            transaction. If there is an older one, we remove it (and update any
            deps. that aren't satisfied by the newer pkg) and return False so
            we'll update to this newer pkg. """
        found = False
        for txmbr in self.tsInfo.getMembersWithState(pkgtup, [TS_UPDATED]):
            count = 0
            for po in txmbr.updated_by:
                if available_pkg.verLE(po):
                    count += 1
                else:
                    for ntxmbr in self.tsInfo.getMembers(po.pkgtup):
                        self.tsInfo.remove(ntxmbr.po.pkgtup)
                        txs = self._check_new_update_provides(ntxmbr.po,
                                                              available_pkg)
                        tx_return.extend(txs)
            if count:
                found = True
            else:
                self.tsInfo.remove(txmbr.po.pkgtup)
        return found

    def _add_up_txmbr(self, requiringPo, upkg, ipkg):
        txmbr = self.tsInfo.addUpdate(upkg, ipkg)
        if requiringPo:
            txmbr.setAsDep(requiringPo)
        if ('reason' in ipkg.yumdb_info and ipkg.yumdb_info.reason == 'dep'):
            txmbr.reason = 'dep'
        return txmbr

    def update(self, po=None, requiringPo=None, **kwargs):
        """try to mark for update the item(s) specified. 
            po is a package object - if that is there, mark it for update,
            if possible
            else use **kwargs to match the package needing update
            if nothing is specified at all then attempt to update everything
            
            returns the list of txmbr of the items it marked for update"""
        
        # check for args - if no po nor kwargs, do them all
        # if po, do it, ignore all else
        # if no po do kwargs
        # uninstalled pkgs called for update get returned with errors in a list, maybe?

        tx_return = []
        if not po and not kwargs: # update everything (the easy case)
            self.verbose_logger.log(logginglevels.DEBUG_2, _('Updating Everything'))
            updates = self.up.getUpdatesTuples()
            if self.conf.obsoletes:
                obsoletes = self.up.getObsoletesTuples(newest=1)
            else:
                obsoletes = []

            for (obsoleting, installed) in obsoletes:
                obsoleting_pkg = self.getPackageObject(obsoleting,
                                                       allow_missing=True)
                if obsoleting_pkg is None:
                    continue
                topkg = self._test_loop(obsoleting_pkg, self._pkg2obspkg)
                if topkg is not None:
                    obsoleting_pkg = topkg
                installed_pkg =  self.getInstalledPackageObject(installed)
                txmbr = self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                if requiringPo:
                    txmbr.setAsDep(requiringPo)
                tx_return.append(txmbr)
                
            for (new, old) in updates:
                if self.tsInfo.isObsoleted(pkgtup=old):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                        old)
                else:
                    new = self.getPackageObject(new, allow_missing=True)
                    if new is None:
                        continue
                    tx_return.extend(self.update(po=new))
            
            return tx_return

        # complications
        # the user has given us something - either a package object to be
        # added to the transaction as an update or they've given us a pattern 
        # of some kind
        
        instpkgs = []
        availpkgs = []
        if po: # just a po
            if po.repoid == 'installed':
                instpkgs.append(po)
            else:
                availpkgs.append(po)
                
                
        elif 'pattern' in kwargs:
            if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                return self._minus_deselect(kwargs['pattern'])

            if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                return self._at_groupinstall(kwargs['pattern'])

            (e, m, u) = self.rpmdb.matchPackageNames([kwargs['pattern']])
            instpkgs.extend(e)
            instpkgs.extend(m)

            if u:
                depmatches = []
                arg = u[0]
                try:
                    depmatches = self.returnInstalledPackagesByDep(arg)
                except yum.Errors.YumBaseError as e:
                    self.logger.critical(_('%s') % e)
                
                instpkgs.extend(depmatches)

            #  Always look for available packages, it doesn't seem to do any
            # harm (apart from some time). And it fixes weird edge cases where
            # "update a" (which requires a new b) is different from "update b"
            try:
                pats = [kwargs['pattern']]
                m = self.pkgSack.returnNewestByNameArch(patterns=pats)
            except Errors.PackageSackError:
                m = []
            availpkgs.extend(m)

            if not availpkgs and not instpkgs:
                self.logger.critical(_('No Match for argument: %s') % arg)
        
        else: # we have kwargs, sort them out.
            nevra_dict = self._nevra_kwarg_parse(kwargs)

            instpkgs = self.rpmdb.searchNevra(name=nevra_dict['name'], 
                        epoch=nevra_dict['epoch'], arch=nevra_dict['arch'], 
                        ver=nevra_dict['version'], rel=nevra_dict['release'])

            if not instpkgs:
                availpkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                            epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                            ver=nevra_dict['version'], rel=nevra_dict['release'])
                if len(availpkgs) > 1:
                    availpkgs = self._compare_providers(availpkgs, requiringPo)
                    availpkgs = map(lambda x: x[0], availpkgs)

       
        # for any thing specified
        # get the list of available pkgs matching it (or take the po)
        # get the list of installed pkgs matching it (or take the po)
        # go through each list and look for:
           # things obsoleting it if it is an installed pkg
           # things it updates if it is an available pkg
           # things updating it if it is an installed pkg
           # in that order
           # all along checking to make sure we:
            # don't update something that's already been obsoleted
            # don't update something that's already been updated
            
        # if there are more than one package that matches an update from
        # a pattern/kwarg then:
            # if it is a valid update and we'
        
        # TODO: we should search the updates and obsoletes list and
        # mark the package being updated or obsoleted away appropriately
        # and the package relationship in the tsInfo
        

        # check for obsoletes first
        if self.conf.obsoletes:
            for installed_pkg in instpkgs:
                obs_tups = self.up.obsoleted_dict.get(installed_pkg.pkgtup, [])
                # This is done so we don't have to returnObsoletes(newest=True)
                # It's a minor UI problem for RHEL, but might as well dtrt.
                obs_pkgs = []
                for pkgtup in obs_tups:
                    obsoleting_pkg = self.getPackageObject(pkgtup,
                                                           allow_missing=True)
                    if obsoleting_pkg is None:
                        continue
                    obs_pkgs.append(obsoleting_pkg)
                for obsoleting_pkg in packagesNewestByName(obs_pkgs):
                    tx_return.extend(self.install(po=obsoleting_pkg))
            for available_pkg in availpkgs:
                for obsoleted_pkg in self._find_obsoletees(available_pkg):
                    obsoleted = obsoleted_pkg.pkgtup
                    txmbr = self.tsInfo.addObsoleting(available_pkg, obsoleted_pkg)
                    if requiringPo:
                        txmbr.setAsDep(requiringPo)
                    tx_return.append(txmbr)
                    if self.tsInfo.isObsoleted(obsoleted):
                        self.verbose_logger.log(logginglevels.DEBUG_2, _('Package is already obsoleted: %s.%s %s:%s-%s') % obsoleted)
                    else:
                        txmbr = self.tsInfo.addObsoleted(obsoleted_pkg, available_pkg)
                        tx_return.append(txmbr)

        for installed_pkg in instpkgs:
            for updating in self.up.updatesdict.get(installed_pkg.pkgtup, []):
                po = self.getPackageObject(updating, allow_missing=True)
                if po is None:
                    continue
                if self.tsInfo.isObsoleted(installed_pkg.pkgtup):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                                            installed_pkg.pkgtup)                                               
                # at this point we are going to mark the pkg to be installed, make sure
                # it doesn't obsolete anything. If it does, mark that in the tsInfo, too
                elif po.pkgtup in self.up.getObsoletesList(name=po.name):
                    for obsoletee in self._find_obsoletees(po):
                        txmbr = self.tsInfo.addUpdate(po, installed_pkg)
                        if requiringPo:
                            txmbr.setAsDep(requiringPo)
                        self.tsInfo.addObsoleting(po, obsoletee)
                        self.tsInfo.addObsoleted(obsoletee, po)
                        tx_return.append(txmbr)
                else:
                    txmbr = self._add_up_txmbr(requiringPo, po, installed_pkg)
                    tx_return.append(txmbr)
                        
        for available_pkg in availpkgs:
            #  Make sure we're not installing a package which is obsoleted by
            # something else in the repo. Unless there is a obsoletion loop,
            # at which point ignore everything.
            obsoleting_pkg = self._test_loop(available_pkg, self._pkg2obspkg)
            if obsoleting_pkg is not None:
                self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is obsoleted: %s'), available_pkg)
                tx_return.extend(self.update(po=obsoleting_pkg))
                continue
            for updated in self.up.updating_dict.get(available_pkg.pkgtup, []):
                if self.tsInfo.isObsoleted(updated):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                                            updated)
                elif self._newer_update_in_trans(updated, available_pkg,
                                                 tx_return):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already updated: %s.%s %s:%s-%s') %
                                            updated)
                
                else:
                    updated_pkg =  self.getInstalledPackageObject(updated)
                    txmbr = self._add_up_txmbr(requiringPo,
                                               available_pkg, updated_pkg)
                    tx_return.append(txmbr)
                    
            # check to see if the pkg we want to install is not _quite_ the newest
            # one but still technically an update over what is installed.
            #FIXME - potentially do the comparables thing from what used to
            #        be in cli.installPkgs() to see what we should be comparing
            #        it to of what is installed. in the meantime name.arch is
            #        most likely correct
            pot_updated = self.rpmdb.searchNevra(name=available_pkg.name, arch=available_pkg.arch)
            if pot_updated and self.allowedMultipleInstalls(available_pkg):
                # only compare against the newest of what's installed for kernel
                pot_updated = sorted(pot_updated)[-1:]

            for ipkg in pot_updated:
                if self.tsInfo.isObsoleted(ipkg.pkgtup):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                                            ipkg.pkgtup)
                elif self._newer_update_in_trans(ipkg.pkgtup, available_pkg,
                                                 tx_return):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already updated: %s.%s %s:%s-%s') %
                                            ipkg.pkgtup)
                elif ipkg.verLT(available_pkg):
                    txmbr = self._add_up_txmbr(requiringPo, available_pkg, ipkg)
                    tx_return.append(txmbr)
        
        for txmbr in tx_return:
            for i_pkg in self.rpmdb.searchNevra(name=txmbr.name):
                if i_pkg not in txmbr.updates:
                    if self._does_this_update(txmbr.po, i_pkg):
                        self.tsInfo.addUpdated(i_pkg, txmbr.po)
                        
        return tx_return
        
    def remove(self, po=None, **kwargs):
        """try to find and mark for remove the specified package(s) -
            if po is specified then that package object (if it is installed) 
            will be marked for removal.
            if no po then look at kwargs, if neither then raise an exception"""

        if not po and not kwargs:
            raise Errors.RemoveError('Nothing specified to remove')#, 'Nothing specified to remove'
        
        tx_return = []
        pkgs = []
        
        
        if po:
            pkgs = [po]  
        else:
            if 'pattern' in kwargs:
                if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                    return self._minus_deselect(kwargs['pattern'])

                if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                    return self._at_groupremove(kwargs['pattern'])

                (e,m,u) = self.rpmdb.matchPackageNames([kwargs['pattern']])
                pkgs.extend(e)
                pkgs.extend(m)
                if u:
                    depmatches = []
                    arg = u[0]
                    try:
                        depmatches = self.returnInstalledPackagesByDep(arg)
                    except yum.Errors.YumBaseError as e:
                        self.logger.critical(_('%s') % e)
                    
                    if not depmatches:
                        self.logger.critical(_('No Match for argument: %s') % arg)
                    else:
                        pkgs.extend(depmatches)
                
            else:    
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                pkgs = self.rpmdb.searchNevra(name=nevra_dict['name'], 
                            epoch=nevra_dict['epoch'], arch=nevra_dict['arch'], 
                            ver=nevra_dict['version'], rel=nevra_dict['release'])

                if len(pkgs) == 0:
                    if not kwargs.get('silence_warnings', False):
                        self.logger.warning(_("No package matched to remove"))

        ts = self.rpmdb.readOnlyTS()
        kern_pkgtup = misc.get_running_kernel_pkgtup(ts)
        for po in pkgs:
            if self.conf.protected_packages and po.pkgtup == kern_pkgtup:
                self.logger.warning(_("Skipping the running kernel: %s") % po)
                continue
            txmbr = self.tsInfo.addErase(po)
            tx_return.append(txmbr)
        
        return tx_return

    def installLocal(self, pkg, po=None, updateonly=False):
        """
        handles installs/updates of rpms provided on the filesystem in a
        local dir (ie: not from a repo)

        Return the added transaction members.

        @param pkg: a path to an rpm file on disk.
        @param po: A YumLocalPackage
        @param updateonly: Whether or not true installs are valid.
        """

        # read in the package into a YumLocalPackage Object
        # append it to self.localPackages
        # check if it can be installed or updated based on nevra versus rpmdb
        # don't import the repos until we absolutely need them for depsolving
        tx_return = []
        installpkgs = []
        updatepkgs = []
        donothingpkgs = []

        if not po:
            try:
                po = YumUrlPackage(self, ts=self.rpmdb.readOnlyTS(), url=pkg,
                                   ua=default_grabber.opts.user_agent)
            except Errors.MiscError:
                self.logger.critical(_('Cannot open: %s. Skipping.'), pkg)
                return tx_return
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Examining %s: %s'), po.localpath, po)

        # apparently someone wanted to try to install a drpm as an rpm :(
        if po.hdr['payloadformat'] == 'drpm':
            self.logger.critical(_('Cannot localinstall deltarpm: %s. Skipping.'), pkg)
            return tx_return

        # if by any chance we're a noncompat arch rpm - bail and throw out an error
        # FIXME -our archlist should be stored somewhere so we don't have to
        # do this: but it's not a config file sort of thing
        # FIXME: Should add noarch, yum localinstall works ...
        # just rm this method?
        if po.arch not in self.arch.archlist:
            self.logger.critical(_('Cannot add package %s to transaction. Not a compatible architecture: %s'), pkg, po.arch)
            return tx_return
        
        if self.conf.obsoletes:
            obsoleters = po.obsoletedBy(self.rpmdb.searchObsoletes(po.name))
            if obsoleters:
                self.logger.critical(_('Cannot install package %s. It is obsoleted by installed package %s'), po, obsoleters[0])
                return tx_return
            
        # everything installed that matches the name
        installedByKey = self.rpmdb.searchNevra(name=po.name)
        # go through each package
        if len(installedByKey) == 0: # nothing installed by that name
            if updateonly:
                self.logger.warning(_('Package %s not installed, cannot update it. Run yum install to install it instead.'), po.name)
                return tx_return
            else:
                installpkgs.append(po)

        for installed_pkg in installedByKey:
            if po.verGT(installed_pkg): # we're newer - this is an update, pass to them
                if installed_pkg.name in self.conf.exactarchlist:
                    if po.arch == installed_pkg.arch:
                        updatepkgs.append((po, installed_pkg))
                    else:
                        donothingpkgs.append(po)
                else:
                    updatepkgs.append((po, installed_pkg))
            elif po.verEQ(installed_pkg):
                if (po.arch != installed_pkg.arch and
                    (isMultiLibArch(po.arch) or
                     isMultiLibArch(installed_pkg.arch))):
                    installpkgs.append(po)
                else:
                    donothingpkgs.append(po)
            elif self.allowedMultipleInstalls(po):
                installpkgs.append(po)
            else:
                donothingpkgs.append(po)

        # handle excludes for a localinstall
        check_pkgs = installpkgs + [x[0] for x in updatepkgs]
        if self._is_local_exclude(po, check_pkgs):
            self.verbose_logger.debug(_('Excluding %s'), po)
            return tx_return

        for po in installpkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Marking %s to be installed'), po.localpath)
            self.localPackages.append(po)
            tx_return.extend(self.install(po=po))

        for (po, oldpo) in updatepkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Marking %s as an update to %s'), po.localpath, oldpo)
            self.localPackages.append(po)
            txmbrs = self.update(po=po)
            tx_return.extend(txmbrs)

        for po in donothingpkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('%s: does not update installed package.'), po.localpath)
        
        # this checks to make sure that any of the to-be-installed pkgs
        # does not obsolete something else that's installed
        # this doesn't handle the localpkgs obsoleting EACH OTHER or
        # anything else in the transaction set, though. That could/should
        # be fixed later but a fair bit of that is a pebkac and should be
        # said as "don't do that". potential 'fixme'
        for txmbr in tx_return:
            #  We don't want to do this twice, so only bother if the txmbr
            # doesn't already obsolete anything.
            if txmbr.po.obsoletes and not txmbr.obsoletes:
                for obs_pkg in self._find_obsoletees(txmbr.po):
                    self.tsInfo.addObsoleted(obs_pkg, txmbr.po)
                    txmbr.obsoletes.append(obs_pkg)
                    self.tsInfo.addObsoleting(txmbr.po,obs_pkg)
                
        return tx_return

    def reinstallLocal(self, pkg, po=None):
        """
        handles reinstall of rpms provided on the filesystem in a
        local dir (ie: not from a repo)

        Return the added transaction members.

        @param pkg: a path to an rpm file on disk.
        @param po: A YumLocalPackage
        """

        if not po:
            try:
                po = YumUrlPackage(self, ts=self.rpmdb.readOnlyTS(), url=pkg,
                                   ua=default_grabber.opts.user_agent)
            except Errors.MiscError:
                self.logger.critical(_('Cannot open file: %s. Skipping.'), pkg)
                return []
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Examining %s: %s'), po.localpath, po)

        if po.arch not in self.arch.archlist:
            self.logger.critical(_('Cannot add package %s to transaction. Not a compatible architecture: %s'), pkg, po.arch)
            return []

        # handle excludes for a local reinstall
        if self._is_local_exclude(po, [po]):
            self.verbose_logger.debug(_('Excluding %s'), po)
            return []

        return self.reinstall(po=po)

    def reinstall(self, po=None, **kwargs):
        """Setup the problem filters to allow a reinstall to work, then
           pass everything off to install"""
           
        self._add_prob_flags(rpm.RPMPROB_FILTER_REPLACEPKG,
                             rpm.RPMPROB_FILTER_REPLACENEWFILES,
                             rpm.RPMPROB_FILTER_REPLACEOLDFILES)

        tx_mbrs = []
        if po: # The po, is the "available" po ... we want the installed po
            tx_mbrs.extend(self.remove(pkgtup=po.pkgtup))
        else:
            tx_mbrs.extend(self.remove(**kwargs))
        if not tx_mbrs:
            raise Errors.ReinstallRemoveError(_("Problem in reinstall: no package matched to remove"))#, _("Problem in reinstall: no package matched to remove")
        templen = len(tx_mbrs)
        # this is a reinstall, so if we can't reinstall exactly what we uninstalled
        # then we really shouldn't go on
        new_members = []
        failed = []
        failed_pkgs = []
        for item in tx_mbrs[:]:
            #  Make sure obsoletes processing is off, so we can reinstall()
            # pkgs that are obsolete.
            old_conf_obs = self.conf.obsoletes
            self.conf.obsoletes = False
            if isinstance(po, YumLocalPackage):
                members = self.install(po=po)
            else:
                members = self.install(pkgtup=item.pkgtup)
            self.conf.obsoletes = old_conf_obs
            if len(members) == 0:
                self.tsInfo.remove(item.pkgtup)
                tx_mbrs.remove(item)
                failed.append(str(item.po))
                failed_pkgs.append(item.po)
                continue
            new_members.extend(members)

        if failed and not tx_mbrs:
            raise Errors.ReinstallInstallError(_("Problem in reinstall: no package %s matched to install") % ", ".join(failed), failed_pkgs=failed_pkgs)
        tx_mbrs.extend(new_members)
        return tx_mbrs
        
    def downgradeLocal(self, pkg, po=None):
        """
        handles downgrades of rpms provided on the filesystem in a
        local dir (ie: not from a repo)

        Return the added transaction members.

        @param pkg: a path to an rpm file on disk.
        @param po: A YumLocalPackage
        """

        if not po:
            try:
                po = YumUrlPackage(self, ts=self.rpmdb.readOnlyTS(), url=pkg,
                                   ua=default_grabber.opts.user_agent)
            except Errors.MiscError:
                self.logger.critical(_('Cannot open file: %s. Skipping.'), pkg)
                return []
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Examining %s: %s'), po.localpath, po)

        if po.arch not in self.arch.archlist:
            self.logger.critical(_('Cannot add package %s to transaction. Not a compatible architecture: %s'), pkg, po.arch)
            return []

        # handle excludes for a local downgrade
        if self._is_local_exclude(po, [po]):
            self.verbose_logger.debug(_('Excluding %s'), po)
            return []

        return self.downgrade(po=po)

    def _is_local_exclude(self, po, pkglist):
        """returns True if the local pkg should be excluded"""
        
        if "all" in self.conf.disable_excludes or \
           "main" in self.conf.disable_excludes:
            return False
        
        toexc = []
        if len(self.conf.exclude) > 0:
            exactmatch, matched, unmatched = \
                   parsePackages(pkglist, self.conf.exclude, casematch=1)
            toexc = exactmatch + matched

        if po in toexc:
            return True

        return False
        
    def downgrade(self, po=None, **kwargs):
        """ Try to downgrade a package. Works like:
            % yum shell <<EOL
            remove  abcd
            install abcd-<old-version>
            run
            EOL """

        if not po and not kwargs:
            raise Errors.DowngradeError('Nothing specified to remove')#, 'Nothing specified to remove'

        doing_group_pkgs = False
        if po:
            apkgs = [po]
        elif 'pattern' in kwargs:
            if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                return self._minus_deselect(kwargs['pattern'])

            if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                apkgs = self._at_groupdowngrade(kwargs['pattern'])
                doing_group_pkgs = True # Don't warn. about some things
            else:
                apkgs = self.pkgSack.returnPackages(patterns=[kwargs['pattern']],
                                                   ignore_case=False)
                if not apkgs:
                    arg = kwargs['pattern']
                    self.verbose_logger.debug(_('Checking for virtual provide or file-provide for %s'), 
                        arg)

                    try:
                        apkgs = self.returnPackagesByDep(arg)
                    except yum.Errors.YumBaseError as e:
                        self.logger.critical(_('No Match for argument: %s') % arg)

        else:
            nevra_dict = self._nevra_kwarg_parse(kwargs)
            apkgs = self.pkgSack.searchNevra(name=nevra_dict['name'], 
                                             epoch=nevra_dict['epoch'],
                                             arch=nevra_dict['arch'], 
                                             ver=nevra_dict['version'],
                                             rel=nevra_dict['release'])
        if not apkgs:
            # Do we still want to return errors here?
            # We don't in the cases below, so I didn't here...
            pkgs = []
            if 'pattern' in kwargs:
                pkgs = self.rpmdb.returnPackages(patterns=[kwargs['pattern']],
                                                 ignore_case=False)
            if 'name' in kwargs:
                pkgs = self.rpmdb.searchNevra(name=kwargs['name'])
            if pkgs:
                return []
            raise Errors.DowngradeError(_('No package(s) available to downgrade'))#, _('No package(s) available to downgrade')

        warned_nas = set()
        # Skip kernel etc.
        tapkgs = []
        for pkg in apkgs:
            if self.allowedMultipleInstalls(pkg):
                if (pkg.name, pkg.arch) not in warned_nas:
                    msg = _("Package %s is allowed multiple installs, skipping") % pkg
                    self.verbose_logger.log(logginglevels.INFO_2, msg)
                warned_nas.add((pkg.name, pkg.arch))
                continue
            tapkgs.append(pkg)
        apkgs = tapkgs

        # Find installed versions of "to downgrade pkgs"
        apkg_names = set()
        for pkg in apkgs:
            apkg_names.add(pkg.name)
        ipkgs = self.rpmdb.searchNames(list(apkg_names))

        latest_installed_na = {}
        latest_installed_n  = {}
        for pkg in sorted(ipkgs):
            if (pkg.name not in latest_installed_n or
                pkg.verGT(latest_installed_n[pkg.name][0])):
                latest_installed_n[pkg.name] = [pkg]
            elif pkg.verEQ(latest_installed_n[pkg.name][0]):
                latest_installed_n[pkg.name].append(pkg)
            latest_installed_na[(pkg.name, pkg.arch)] = pkg

        #  Find "latest downgrade", ie. latest available pkg before
        # installed version. Indexed fromn the latest installed pkgtup.
        downgrade_apkgs = {}
        for pkg in sorted(apkgs):
            na  = (pkg.name, pkg.arch)

            # Here we allow downgrades from .i386 => .noarch, or .i586 => .i386
            # but not .i386 => .x86_64 (similar to update).
            lipkg = None
            if na in latest_installed_na:
                lipkg = latest_installed_na[na]
            elif pkg.name in latest_installed_n:
                for tlipkg in latest_installed_n[pkg.name]:
                    if not canCoinstall(pkg.arch, tlipkg.arch):
                        lipkg = tlipkg
                        #  Use this so we don't get confused when we have
                        # different versions with different arches.
                        na = (pkg.name, lipkg.arch)
                        break

            if lipkg is None:
                if (na not in warned_nas and not doing_group_pkgs and
                    pkg.name not in latest_installed_n):
                    msg = _('No Match for available package: %s') % pkg
                    self.logger.critical(msg)
                warned_nas.add(na)
                continue

            if pkg.verGE(lipkg):
                if na not in warned_nas:
                    msg = _('Only Upgrade available on package: %s') % pkg
                    self.logger.critical(msg)
                warned_nas.add(na)
                continue

            warned_nas.add(na)
            if (lipkg.pkgtup in downgrade_apkgs and
                pkg.verLE(downgrade_apkgs[lipkg.pkgtup])):
                continue # Skip older than "latest downgrade"
            downgrade_apkgs[lipkg.pkgtup] = pkg

        tx_return = []
        for ipkg in ipkgs:
            if ipkg.pkgtup not in downgrade_apkgs:
                continue
            txmbrs = self.tsInfo.addDowngrade(downgrade_apkgs[ipkg.pkgtup],ipkg)
            if not txmbrs: # Fail?
                continue
            self._add_prob_flags(rpm.RPMPROB_FILTER_OLDPACKAGE)
            tx_return.extend(txmbrs)

        return tx_return
        
    def _nevra_kwarg_parse(self, kwargs):
            
        returndict = {}
        
        if 'pkgtup' in kwargs:
            (n, a, e, v, r) = kwargs['pkgtup']
            returndict['name'] = n
            returndict['epoch'] = e
            returndict['arch'] = a
            returndict['version'] = v
            returndict['release'] = r
            return returndict

        returndict['name'] = kwargs.get('name')
        returndict['epoch'] = kwargs.get('epoch')
        returndict['arch'] = kwargs.get('arch')
        # get them as ver, version and rel, release - if someone
        # specifies one of each then that's kinda silly.
        returndict['version'] = kwargs.get('version')
        if returndict['version'] is None:
            returndict['version'] = kwargs.get('ver')

        returndict['release'] = kwargs.get('release')
        if returndict['release'] is None:
            returndict['release'] = kwargs.get('rel')

        return returndict

    def history_redo(self, transaction):
        """ Given a valid historical transaction object, try and repeat
            that transaction. """
        # NOTE: This is somewhat basic atm. ... see comment in undo.
        #  Also note that redo doesn't force install Dep-Install packages,
        # which is probably what is wanted the majority of the time.
        old_conf_obs = self.conf.obsoletes
        self.conf.obsoletes = False
        done = False
        for pkg in transaction.trans_data:
            if pkg.state == 'Reinstall':
                if self.reinstall(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Downgrade':
                try:
                    if self.downgrade(pkgtup=pkg.pkgtup):
                        done = True
                except yum.Errors.DowngradeError:
                    self.logger.critical(_('Failed to downgrade: %s'), pkg)
        for pkg in transaction.trans_data:
            if pkg.state == 'Update':
                if self.update(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state in ('Install', 'True-Install', 'Obsoleting'):
                if self.install(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Erase':
                if self.remove(pkgtup=pkg.pkgtup):
                    done = True
        self.conf.obsoletes = old_conf_obs
        return done

    def history_undo(self, transaction):
        """ Given a valid historical transaction object, try and undo
            that transaction. """
        # NOTE: This is somewhat basic atm. ... for instance we don't check
        #       that we are going from the old new version. However it's still
        #       better than the RHN rollback code, and people pay for that :).
        #  We turn obsoletes off because we want the specific versions of stuff
        # from history ... even if they've been obsoleted since then.
        old_conf_obs = self.conf.obsoletes
        self.conf.obsoletes = False
        done = False
        for pkg in transaction.trans_data:
            if pkg.state == 'Reinstall':
                if self.reinstall(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Updated':
                try:
                    if self.downgrade(pkgtup=pkg.pkgtup):
                        done = True
                except yum.Errors.DowngradeError:
                    self.logger.critical(_('Failed to downgrade: %s'), pkg)
        for pkg in transaction.trans_data:
            if pkg.state == 'Downgraded':
                if self.update(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Obsoleting':
                if self.remove(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state in ('Dep-Install', 'Install', 'True-Install'):
                if self.remove(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Obsoleted':
                if self.install(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Erase':
                if self.install(pkgtup=pkg.pkgtup):
                    done = True
        self.conf.obsoletes = old_conf_obs
        return done

    def _retrievePublicKey(self, keyurl, repo=None):
        """
        Retrieve a key file
        @param keyurl: url to the key to retrieve
        Returns a list of dicts with all the keyinfo
        """
        key_installed = False

        self.logger.info(_('Retrieving GPG key from %s') % keyurl)

        # Go get the GPG key from the given URL
        try:
            url = misc.to_utf8(keyurl)
            if repo is None:
                rawkey = urlgrabber.urlread(url, limit=9999)
            else:
                #  If we have a repo. use the proxy etc. configuration for it.
                # In theory we have a global proxy config. too, but meh...
                # external callers should just update.
                ug = URLGrabber(bandwidth = repo.bandwidth,
                                retry = repo.retries,
                                throttle = repo.throttle,
                                progress_obj = repo.callback,
                                proxies=repo.proxy_dict)
                ug.opts.user_agent = default_grabber.opts.user_agent
                rawkey = ug.urlread(url, text=repo.id + "/gpgkey")

        except urlgrabber.grabber.URLGrabError as e:
            raise Errors.YumBaseError(_('GPG key retrieval failed: ') +
                                      to_unicode(str(e)))
        # Parse the key
        try:
            keys_info = misc.getgpgkeyinfo(rawkey, multiple=True)
        except ValueError as e:
            raise Errors.YumBaseError(_('Invalid GPG Key from %s: %s') % 
                                      (url, to_unicode(str(e))))
        keys = []
        for keyinfo in keys_info:
            thiskey = {}
            for info in ('keyid', 'timestamp', 'userid', 
                         'fingerprint', 'raw_key'):
                if info not in keyinfo:
                    raise Errors.YumBaseError(_('GPG key parsing failed: key does not have value %s') + info)#, \
                    #   _('GPG key parsing failed: key does not have value %s') + info
                thiskey[info] = keyinfo[info]
            thiskey['hexkeyid'] = misc.keyIdToRPMVer(keyinfo['keyid']).upper()
            keys.append(thiskey)
        
        return keys

    def _getKeyImportMessage(self, info, keyurl):
        msg = None
        if keyurl.startswith("file:"):
            fname = keyurl[len("file:"):]
            pkgs = self.rpmdb.searchFiles(fname)
            if pkgs:
                pkgs = sorted(pkgs)[-1]
                msg = (_('Importing GPG key 0x%s:\n'
                         ' Userid : %s\n'
                         ' Package: %s (%s)\n'
                         ' From   : %s') %
                       (info['hexkeyid'], to_unicode(info['userid']),
                        pkgs, pkgs.ui_from_repo,
                        keyurl.replace("file://","")))
        if msg is None:
            msg = (_('Importing GPG key 0x%s:\n'
                     ' Userid: "%s"\n'
                     ' From  : %s') %
                   (info['hexkeyid'], to_unicode(info['userid']),
                    keyurl.replace("file://","")))
        self.logger.critical("%s", msg)

    def getKeyForPackage(self, po, askcb = None, fullaskcb = None):
        """
        Retrieve a key for a package. If needed, prompt for if the key should
        be imported using askcb.
        
        @param po: Package object to retrieve the key of.
        @param askcb: Callback function to use for asking for verification.
                      Takes arguments of the po, the userid for the key, and
                      the keyid.
        @param fullaskcb: Callback function to use for asking for verification
                          of a key. Differs from askcb in that it gets passed
                          a dictionary so that we can expand the values passed.
        """
        repo = self.repos.getRepo(po.repoid)
        keyurls = repo.gpgkey
        key_installed = False

        ts = self.rpmdb.readOnlyTS()

        for keyurl in keyurls:
            keys = self._retrievePublicKey(keyurl, repo)

            for info in keys:
                # Check if key is already installed
                if misc.keyInstalled(ts, info['keyid'], info['timestamp']) >= 0:
                    self.logger.info(_('GPG key at %s (0x%s) is already installed') % (
                        keyurl, info['hexkeyid']))
                    continue

                # Try installing/updating GPG key
                self._getKeyImportMessage(info, keyurl)
                rc = False
                if self.conf.assumeyes:
                    rc = True
                elif fullaskcb:
                    rc = fullaskcb({"po": po, "userid": info['userid'],
                                    "hexkeyid": info['hexkeyid'], 
                                    "keyurl": keyurl,
                                    "fingerprint": info['fingerprint'],
                                    "timestamp": info['timestamp']})
                elif askcb:
                    rc = askcb(po, info['userid'], info['hexkeyid'])

                if not rc:
                    raise Errors.YumBaseError(_("Not installing key"))#, _("Not installing key")
                
                # Import the key
                result = ts.pgpImportPubkey(misc.procgpgkey(info['raw_key']))
                if result != 0:
                    raise Errors.YumBaseError(_('Key import failed (code %d)') % result)#, \
                          
                self.logger.info(_('Key imported successfully'))
                key_installed = True

        if not key_installed:
            raise Errors.YumBaseError(_('The GPG keys listed for the "%s" repository are already installed but they are not correct for this package.Check that the correct key URLs are configured for this repository.') % (repo.name))#, \
                #   _('The GPG keys listed for the "%s" repository are ' \
                #   'already installed but they are not correct for this ' \
                #   'package.\n' \
                #   'Check that the correct key URLs are configured for ' \
                #   'this repository.') % (repo.name)

        # Check if the newly installed keys helped
        result, errmsg = self.sigCheckPkg(po)
        if result != 0:
            self.logger.info(_("Import of key(s) didn't help, wrong key(s)?"))
            raise Errors.YumBaseError(errmsg)#, errmsg
    
    def getKeyForRepo(self, repo, callback=None):
        """
        Retrieve a key for a repository If needed, prompt for if the key should
        be imported using callback
        
        @param repo: Repository object to retrieve the key of.
        @param callback: Callback function to use for asking for verification
                          of a key. Takes a dictionary of key info.
        """
        keyurls = repo.gpgkey
        key_installed = False
        for keyurl in keyurls:
            keys = self._retrievePublicKey(keyurl, repo)
            for info in keys:
                # Check if key is already installed
                if info['keyid'] in misc.return_keyids_from_pubring(repo.gpgdir):
                    self.logger.info(_('GPG key at %s (0x%s) is already imported') % (
                        keyurl, info['hexkeyid']))
                    continue

                # Try installing/updating GPG key
                self._getKeyImportMessage(info, keyurl)
                rc = False
                if self.conf.assumeyes:
                    rc = True
                elif callback:
                    rc = callback({"repo": repo, "userid": info['userid'],
                                    "hexkeyid": info['hexkeyid'], "keyurl": keyurl,
                                    "fingerprint": info['fingerprint'],
                                    "timestamp": info['timestamp']})


                if not rc:
                    raise Errors.YumBaseError(_("Not installing key for repo %s") % repo)#, _("Not installing key for repo %s") % repo
                
                # Import the key
                result = misc.import_key_to_pubring(info['raw_key'], info['hexkeyid'], gpgdir=repo.gpgdir)
                if not result:
                    raise Errors.YumBaseError(_('Key import failed'))#, _('Key import failed')
                self.logger.info(_('Key imported successfully'))
                key_installed = True

        if not key_installed:
            raise Errors.YumBaseError(_('The GPG keys listed for the "%s" repository are already installed but they are not correct.Check that the correct key URLs are configured for this repository.') % (repo.name))#, \
                #   _('The GPG keys listed for the "%s" repository are ' \
                #   'already installed but they are not correct.\n' \
                #   'Check that the correct key URLs are configured for ' \
                #   'this repository.') % (repo.name)

    def _limit_installonly_pkgs(self):
        """ Limit packages based on conf.installonly_limit, if any of the
            packages being installed have a provide in conf.installonlypkgs.
            New in 3.2.24: Obey yumdb_info.installonly data. """

        def _sort_and_filter_installonly(pkgs):
            """ Allow the admin to specify some overrides fo installonly pkgs.
                using the yumdb. """
            ret_beg = []
            ret_mid = []
            ret_end = []
            for pkg in sorted(pkgs):
                if 'installonly' not in pkg.yumdb_info:
                    ret_mid.append(pkg)
                    continue

                if pkg.yumdb_info.installonly == 'keep':
                    continue

                if True: # Don't to magic sorting, yet
                    ret_mid.append(pkg)
                    continue

                if pkg.yumdb_info.installonly == 'remove-first':
                    ret_beg.append(pkg)
                elif pkg.yumdb_info.installonly == 'remove-last':
                    ret_end.append(pkg)
                else:
                    ret_mid.append(pkg)

            return ret_beg + ret_mid + ret_end

        if self.conf.installonly_limit < 1 :
            return 
            
        toremove = []
        #  We "probably" want to use either self.ts or self.rpmdb.ts if either
        # is available. However each ts takes a ref. on signals generally, and
        # SIGINT specifically, so we _must_ have got rid of all of the used tses
        # before we try downloading. This is called from buildTransaction()
        # so self.rpmdb.ts should be valid.
        ts = self.rpmdb.readOnlyTS()
        (cur_kernel_v, cur_kernel_r) = misc.get_running_kernel_version_release(ts)
        install_only_names = set(self.conf.installonlypkgs)
        for m in self.tsInfo.getMembers():
            if m.ts_state not in ('i', 'u'):
                continue
            if m.reinstall:
                continue

            po_names = set([m.name] + m.po.provides_names)
            if not po_names.intersection(install_only_names):
                continue

            installed = self.rpmdb.searchNevra(name=m.name)
            installed = _sort_and_filter_installonly(installed)
            if len(installed) < self.conf.installonly_limit - 1:
                continue # we're adding one

            numleft = len(installed) - self.conf.installonly_limit + 1
            for po in installed:
                if (po.version, po.release) == (cur_kernel_v, cur_kernel_r): 
                    # don't remove running
                    continue
                if numleft == 0:
                    break
                toremove.append((po,m))
                numleft -= 1
                        
        for po,rel in toremove:
            txmbr = self.tsInfo.addErase(po)
            # Add a dep relation to the new version of the package, causing this one to be erased
            # this way skipbroken, should clean out the old one, if the new one is skipped
            txmbr.depends_on.append(rel)

    def processTransaction(self, callback=None,rpmTestDisplay=None, rpmDisplay=None):
        '''
        Process the current Transaction
        - Download Packages
        - Check GPG Signatures.
        - Run Test RPM Transaction
        - Run RPM Transaction
        
        callback.event method is called at start/end of each process.
        
        @param callback: callback object (must have an event method)
        @param rpmTestDisplay: Name of display class to use in RPM Test Transaction 
        @param rpmDisplay: Name of display class to use in RPM Transaction 
        '''
        
        if not callback:
            callback = callbacks.ProcessTransNoOutputCallback()
        
        # Download Packages
        callback.event(callbacks.PT_DOWNLOAD)
        pkgs = self._downloadPackages(callback)
        # Check Package Signatures
        if pkgs != None:
            callback.event(callbacks.PT_GPGCHECK)
            self._checkSignatures(pkgs,callback)
        # Run Test Transaction
        callback.event(callbacks.PT_TEST_TRANS)
        self._doTestTransaction(callback,display=rpmTestDisplay)
        # Run Transaction
        callback.event(callbacks.PT_TRANSACTION)
        self._doTransaction(callback,display=rpmDisplay)
    
    def _downloadPackages(self,callback):
        ''' Download the need packages in the Transaction '''
        # This can be overloaded by a subclass.    
        dlpkgs = map(lambda x: x.po, filter(lambda txmbr:
                                            txmbr.ts_state in ("i", "u"),
                                            self.tsInfo.getMembers()))
        # Check if there is something to do
        if len(dlpkgs) == 0:
            return None
        # make callback with packages to download                                    
        callback.event(callbacks.PT_DOWNLOAD_PKGS,dlpkgs)
        try:
            probs = self.downloadPkgs(dlpkgs)

        except IndexError:
            raise Errors.YumBaseError([_("Unable to find a suitable mirror.")])#, [_("Unable to find a suitable mirror.")]
        if len(probs) > 0:
            errstr = [_("Errors were encountered while downloading packages.")]
            for key in probs:
                errors = misc.unique(probs[key])
                for error in errors:
                    errstr.append("%s: %s" % (key, error))

            raise Errors.YumDownloadError(errstr)#, errstr
        return dlpkgs

    def _checkSignatures(self,pkgs,callback):
        ''' The the signatures of the downloaded packages '''
        # This can be overloaded by a subclass.    
        for po in pkgs:
            result, errmsg = self.sigCheckPkg(po)
            if result == 0:
                # Verified ok, or verify not req'd
                continue            
            elif result == 1:
                self.getKeyForPackage(po, self._askForGPGKeyImport)
            else:
                raise Errors.YumGPGCheckError(errmsg)#, errmsg

        return 0
        
    def _askForGPGKeyImport(self, po, userid, hexkeyid):
        ''' 
        Ask for GPGKeyImport 
        This need to be overloaded in a subclass to make GPG Key import work
        '''
        return False
    
    def _doTestTransaction(self,callback,display=None):
        ''' Do the RPM test transaction '''
        # This can be overloaded by a subclass.    
        if self.conf.rpm_check_debug:
            self.verbose_logger.log(logginglevels.INFO_2, 
                 _('Running rpm_check_debug'))
            msgs = self._run_rpm_check_debug()
            if msgs:
                rpmlib_only = True
                for msg in msgs:
                    if msg.startswith('rpmlib('):
                        continue
                    rpmlib_only = False
                if rpmlib_only:
                    retmsgs = [_("ERROR You need to update rpm to handle:")]
                    retmsgs.extend(msgs)
                    raise Errors.YumRPMCheckError(retmsgs)#, retmsgs
                retmsgs = [_('ERROR with rpm_check_debug vs depsolve:')]
                retmsgs.extend(msgs) 
                retmsgs.append(_('Please report this error at %s') 
                                             % self.conf.bugtracker_url)
                raise Errors.YumRPMCheckError(retmsgs)#,retmsgs
        
        tsConf = {}
        for feature in ['diskspacecheck']: # more to come, I'm sure
            tsConf[feature] = getattr( self.conf, feature )
        #
        testcb = RPMTransaction(self, test=True)
        # overwrite the default display class
        if display:
            testcb.display = display
        # clean out the ts b/c we have to give it new paths to the rpms 
        del self.ts
  
        self.initActionTs()
        # save our dsCallback out
        dscb = self.dsCallback
        self.dsCallback = None # dumb, dumb dumb dumb!
        self.populateTs( keepold=0 ) # sigh
        tserrors = self.ts.test( testcb, conf=tsConf )
        del testcb
  
        if len( tserrors ) > 0:
            errstring =  _('Test Transaction Errors: ')
            for descr in tserrors:
                errstring += '  %s\n' % descr 
            raise Errors.YumTestTransactionError(errstring)#, errstring

        del self.ts
        # put back our depcheck callback
        self.dsCallback = dscb


    def _doTransaction(self,callback,display=None):
        ''' do the RPM Transaction '''
        # This can be overloaded by a subclass.    
        self.initActionTs() # make a new, blank ts to populate
        self.populateTs( keepold=0 ) # populate the ts
        self.ts.check() # required for ordering
        self.ts.order() # order
        cb = RPMTransaction(self,display=SimpleCliCallBack)
        # overwrite the default display class
        if display:
            cb.display = display
        self.runTransaction( cb=cb )

    def _run_rpm_check_debug(self):
        results = []
        # save our dsCallback out
        dscb = self.dsCallback
        self.dsCallback = None # dumb, dumb dumb dumb!
        self.populateTs(test=1)
        self.ts.check()
        for prob in self.ts.problems():
            #  Newer rpm (4.8.0+) has problem objects, older have just strings.
            #  Should probably move to using the new objects, when we can. For
            # now just be compatible.
            results.append(to_str(prob))

        self.dsCallback = dscb
        return results

    def add_enable_repo(self, repoid, baseurls=[], mirrorlist=None, **kwargs):
        """add and enable a repo with just a baseurl/mirrorlist and repoid
           requires repoid and at least one of baseurl and mirrorlist
           additional optional kwargs are:
           variable_convert=bool (defaults to true)
           and any other attribute settable to the normal repo setup
           ex: metadata_expire, enable_groups, gpgcheck, cachedir, etc
           returns the repo object it added"""
        # out of place fixme - maybe we should make this the default repo addition
        # routine and use it from getReposFromConfigFile(), etc.
        newrepo = yumRepo.YumRepository(repoid)
        newrepo.name = repoid
        newrepo.basecachedir = self.conf.cachedir
        var_convert = kwargs.get('variable_convert', True)
        
        if baseurls:
            replaced = []
            if var_convert:
                for baseurl in baseurls:
                    if baseurl:
                        replaced.append(varReplace(baseurl, self.conf.yumvar))
            else:
                replaced = baseurls
            newrepo.baseurl = replaced

        if mirrorlist:
            if var_convert:
                mirrorlist = varReplace(mirrorlist, self.conf.yumvar)
            newrepo.mirrorlist = mirrorlist

        # setup the repo
        newrepo.setup(cache=self.conf.cache)

        # some reasonable defaults, (imo)
        newrepo.enablegroups = True
        newrepo.metadata_expire = 0
        newrepo.gpgcheck = self.conf.gpgcheck
        newrepo.repo_gpgcheck = self.conf.repo_gpgcheck
        newrepo.basecachedir = self.conf.cachedir

        for key in kwargs.keys():
            if not hasattr(newrepo, key): continue # skip the ones which aren't vars
            setattr(newrepo, key, kwargs[key])
        
        # add the new repo
        self.repos.add(newrepo)
        # enable the main repo  
        self.repos.enableRepo(newrepo.id)
        return newrepo

    def setCacheDir(self, force=False, tmpdir=None, reuse=True,
                    suffix='/$basearch/$releasever'):
        ''' Set a new cache dir, using misc.getCacheDir() and var. replace
            on suffix. '''

        if not force and os.geteuid() == 0:
            return True # We are root, not forced, so happy with the global dir.
        if tmpdir is None:
            tmpdir = os.getenv('TMPDIR')
        if tmpdir is None: # Note that TMPDIR isn't exported by default :(
            tmpdir = '/var/tmp'
        try:
            cachedir = misc.getCacheDir(tmpdir, reuse)
        except (IOError, OSError) as e:
            self.logger.critical(_('Could not set cachedir: %s') % str(e))
            cachedir = None
            
        if cachedir is None:
            return False # Tried, but failed, to get a "user" cachedir

        cachedir += varReplace(suffix, self.conf.yumvar)
        if hasattr(self, 'prerepoconf'):
            self.prerepoconf.cachedir = cachedir
        else:
            self.repos.setCacheDir(cachedir)
        self.conf.cachedir = cachedir
        return True # We got a new cache dir

    def _does_this_update(self, pkg1, pkg2):
        """returns True if pkg1 can update pkg2, False if not. 
           This only checks if it can be an update it does not check if
           it is obsoleting or anything else."""
        
        if pkg1.name != pkg2.name:
            return False
        if pkg1.verLE(pkg2):
            return False
        if pkg1.arch not in self.arch.archlist:
            return False
        if rpmUtils.arch.canCoinstall(pkg1.arch, pkg2.arch):
            return False
        if self.allowedMultipleInstalls(pkg1):
            return False
            
        return True    

    def _store_config_in_history(self):
        self.history.write_addon_data('config-main', self.conf.dump())
        myrepos = ''
        for repo in self.repos.listEnabled():
            myrepos += repo.dump()
            myrepos += '\n'
        self.history.write_addon_data('config-repos', myrepos)
        
    def verify_plugins_cb(self, verify_package):
        """ Callback to call a plugin hook for pkg.verify(). """
        self.plugins.run('verify_package', verify_package=verify_package)
        return verify_package

