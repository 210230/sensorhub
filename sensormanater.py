#!/usr/bin/env python

import os
import sys
import time
import serial
import shutil
import threading
import subprocess
from multiprocessing import Process

CHECKING_SECONDS = 3600  # time period to check server
REPO = "https://github.com/210230/sensorhub.git"

def file_identical(file1, file2):
    return file(file1, 'rb').read() == file(file2, 'rb').read()

def valid_files(path):
    files = []
    for f in os.listdir(path):
        if '.git' in f:
            continue
        if f.endswith('.pyc'):
            continue
        files.append(f)
    return files

def run_script(cmd):
    print 'run python script: %s' % cmd
    if not os.path.exists(cmd):
        print 'script %s not found' % cmd
        return None
    return subprocess.Popen('python %s' % cmd, shell=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)

def is_running(p):
    if p is None:
        return False
    p.poll()
    return bool(p.returncode is None)

class SensorHubManager(object):
    def __init__(self, path):
        self.path = path
        self._changed_files = []
        self._deleted_files = []
        self._running_scripts = []
        self._lpath = None
        self._running = True
        self._mt = None
        self._threads = {}

    def start_service(self, scripts_to_run):
        self._running_scripts = scripts_to_run
        for s in self._running_scripts:
            self._threads[s] = run_script(os.path.join(self.path, s))
        self._mt = Process(target = self.monitor_thread)
        self._mt.start()

    def stop_service(self):
        self._running = False
        time.sleep(1)  # wait some time in case it can stop
        self._mt.terminate()
        o, e = self._mt.communicate()
        print 'Terminate main thread: out=%s, err=%s' % (o, e)
        for t in self._threads:
            self._threads[t].kill()
            o, e = self._threads[t].communicate()
            print 'Terminate %s: out=%s, err=%s' % (t, o, e)
        print 'All threads are killed'
        time.sleep(1)

    def monitor_thread(self):
        print 'Starting monitor thread'
        while self._running:
            print 'checking workspace...'
            self.running_scripts_check()
            self.sync_with_server()
            self.check_running_files()
            self.action_required()
            time.sleep(CHECKING_SECONDS)
        print 'Monitor thread is terminated'

    def running_scripts_check(self):
        dead_threads = []
        for t in self._threads:
            if not is_running(self._threads[t]):
                dead_threads.append(t)
        for t in dead_threads:
            del self._threads[t]
            self._threads[t] = run_script(os.path.join(self.path, t))
        if dead_threads:
            print 'Start dead threads: %s' % dead_threads

    def sync_with_server(self):
        t = time.localtime()
        self._lpath = 'tempdir-%04d%02d%02d%02d%02d%02d' % (
            t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
        if os.path.exists(self._lpath):
            os.system('rm -rf %s' % self._lpath)
        os.system('git clone -q %s %s' % (REPO, self._lpath))

    def check_running_files(self):
        print 'running scripts: %s' % self._running_scripts
        self._changed_files = []
        self._deleted_files = []
        if not os.path.exists(self.path):
            os.system('cp -r %s %s' % (self._lpath, self.path))
            self._changed_files.extend(valid_files(self.path))
            print 'create new workspace at %s' % self.path
            return
        for f in valid_files(self._lpath):
            srcf = os.path.join(self._lpath, f)
            dstf = os.path.join(self.path, f)
            if not os.path.exists(dstf) or not file_identical(srcf, dstf):
                os.system("cp %s %s" % (
                    os.path.join(self._lpath, f), self.path))
                self._changed_files.append(f)
        dst_files = valid_files(self.path)
        src_files = valid_files(self._lpath)
        if len(dst_files) > len(src_files):
            for f in dst_files:
                if not f in src_files:
                    os.system('rm -rf %s' % os.path.join(self.path, f))
                    self._deleted_files.append(f)
        if self._changed_files:
            print 'changed files: %s' % self._changed_files
        if self._deleted_files:
            print 'deleted files: %s' % self._deleted_files

    def action_required(self):
        for f in self._running_scripts:
            if f in self._changed_files + self._deleted_files:
                if f in self._threads and is_running(self._threads[f]):
                    self._threads[f].kill()
                    print 'script %s is killed' % f
                del self._threads[f]
                if f in self._changed_files:
                    self._threads[f] = run_script(os.path.join(self.path, f))
                    print 'script %s is started' % f
        os.system('rm -rf %s' % self._lpath)


def main():
    print 'starting Sensor Hub Manager...'
    shm = SensorHubManager(sys.argv[1])
    shm.start_service(sys.argv[2:])

    # keep running until CTRL-C is received
    try:
        time.sleep(3)  # Nothing to do here
    except KeyboardInterrupt:
        shm.stop_service()
        sys.exit(0)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: %s <workspace> <scripts_to_run...>' % sys.argv[0])
        sys.exit(0)
    main()
