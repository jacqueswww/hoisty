#!/usr/bin/env python3
"""A real pyinfra run beside the ansible one: argv, inventory, data delivery."""
import os
import sys
from pathlib import Path

import harness
from harness import WORK

import db
import runner

STORE = None
CTX = {}
SECRET = 'a-pyinfra-only-secret'

DEPLOY = '''from pyinfra import host
from pyinfra.operations import python


def write_it():
    with open(host.data.out_path, 'w') as fh:
        fh.write(host.data.db_password)


python.call(name='the encrypted variable must arrive', function=write_it)
'''


def _setup():
    global STORE
    STORE = harness.new_store()
    checkout = WORK / 'pyinfra-checkout'
    checkout.mkdir(exist_ok=True)
    (checkout / 'inventory.py').write_text("all = [('@local', {})]\n")
    (checkout / 'deploy.py').write_text(DEPLOY)
    CTX['out'] = checkout / 'out.txt'
    with db.deploy_conn() as conn:
        pid = conn.execute('INSERT INTO project (name, working_dir, branch) '
                           "VALUES ('pyproj',?,'main')", (str(checkout),)).lastrowid
        eid = conn.execute(
            'INSERT INTO environment (project_id, name, inventory, playbook, tool) '
            "VALUES (?,'live','inventory.py','deploy.py','pyinfra')", (pid,)).lastrowid
        CTX['project'] = dict(conn.execute('SELECT * FROM project WHERE id=?',
                                           (pid,)).fetchone())
        CTX['env'] = dict(conn.execute('SELECT * FROM environment WHERE id=?',
                                       (eid,)).fetchone())
    CTX['eid'] = eid
    STORE.set('environment', eid, 'db_password', SECRET)
    STORE.set('environment', eid, 'out_path', str(CTX['out']))


def an_environment_says_which_tool_plays_it():
    assert runner.tool_of(CTX['env']) == 'pyinfra'
    assert runner.tool_of({'tool': None}) == 'ansible', 'blank means ansible'
    assert runner.tool_of({}) == 'ansible', 'a row from before the column existed'


def argv_is_built_from_the_same_columns():
    argv = runner.pyinfra_argv(CTX['project'], dict(CTX['env'], limit_hosts='web',
                                                    become=1),
                               '/run/all.py', '/run/k', known_hosts='/run/known')
    work = CTX['project']['working_dir']
    assert argv[:4] == [runner.PYINFRA, '-y', f'{work}/inventory.py',
                        f'{work}/deploy.py'], argv
    for pair in (['--limit', 'web'], ['--sudo'], ['--group-data', '/run/all.py'],
                 ['--key', '/run/k'],
                 ['--data', 'ssh_strict_host_key_checking=yes'],
                 ['--data', 'ssh_known_hosts_file=/run/known']):
        assert any(argv[i:i + len(pair)] == pair for i in range(len(argv))), (pair, argv)


def a_new_host_key_is_never_trusted_on_sight():
    """pyinfra's default is accept-new, which trusts whatever answers first.
    --data outranks the inventory and the repo's own group data, so a checkout
    cannot hand itself a weaker policy."""
    argv = runner.build_argv(CTX['project'], CTX['env'])
    assert 'ssh_strict_host_key_checking=yes' in argv, argv
    assert argv.index('ssh_strict_host_key_checking=yes') > 0


def stored_fields_cannot_become_raw_argv():
    for field, value in (('playbook', '--sudo'), ('inventory', '-vvv'),
                         ('limit_hosts', '--data'),
                         ('playbook', '../../../etc/passwd')):
        try:
            runner.pyinfra_argv(CTX['project'], dict(CTX['env'], **{field: value}))
            raise AssertionError(f'{field}={value} was accepted')
        except ValueError:
            pass


def a_missing_file_is_named_rather_than_dialled():
    """pyinfra reads an inventory path that is not a file as a list of hostnames
    to connect to, so a typo would become an ssh attempt to a path."""
    try:
        runner.pyinfra_argv(CTX['project'], dict(CTX['env'], playbook='nope.py'))
        raise AssertionError('a missing operations file was accepted')
    except ValueError as exc:
        assert 'nope.py' in str(exc) and 'does not exist' in str(exc), exc


def tags_belong_to_ansible_and_are_refused_here():
    for field in ('tags', 'skip_tags'):
        try:
            runner.build_argv(CTX['project'], dict(CTX['env'], **{field: 'web'}))
            raise AssertionError(f'{field} was accepted for pyinfra')
        except ValueError as exc:
            assert 'no tags' in str(exc), exc
    assert runner.refresh_tags(CTX['project'], CTX['env']) is None, \
        'there is nothing to list for pyinfra'


def project_hosts_become_a_python_inventory():
    hosts = [{'name': 'web1', 'address': '10.0.0.1', 'ssh_user': 'ubuntu',
              'groups': 'web,live', 'ssh_host_key': None},
             {'name': 'db1', 'address': None, 'ssh_user': None,
              'groups': 'db', 'ssh_host_key': None}]
    path = runner.write_pyinfra_inventory(str(WORK), hosts)
    text = Path(path).read_text()
    scope = {}
    exec(compile(text, path, 'exec'), scope)
    assert scope['all'] == [('web1', {'ssh_hostname': '10.0.0.1',
                                      'ssh_user': 'ubuntu'}),
                            ('db1', {})], scope['all']
    assert scope['web'] == ['web1'] and scope['db'] == ['db1'], text
    assert scope['live'] == ['web1'], text


def a_variable_pyinfra_cannot_name_is_refused():
    """Data becomes a Python name in the group data file; a store is not that
    strict, so the run has to say so rather than write a broken file."""
    try:
        runner.write_pyinfra_data(str(WORK), {'not a name': 'x'})
        raise AssertionError('a name with a space was accepted')
    except ValueError as exc:
        assert 'Python name' in str(exc), exc


def the_data_file_is_readable_only_by_us():
    where = WORK / 'datadir'
    where.mkdir(exist_ok=True)
    path = runner.write_pyinfra_data(str(where), {'a': 1, 'b': [2, 'x']})
    assert os.stat(path).st_mode & 0o777 == 0o600, oct(os.stat(path).st_mode)
    assert Path(path).name == 'all.py', 'the group data file names the all group'
    scope = {}
    exec(compile(Path(path).read_text(), path, 'exec'), scope)
    assert scope['a'] == 1 and scope['b'] == [2, 'x'], scope


def a_real_pyinfra_deploy_runs():
    before = set(db.RUN_DIR.iterdir())
    job_id = runner.deploy(CTX['project'], CTX['env'], STORE, 'tester')
    with db.deploy_conn() as conn:
        job = dict(conn.execute('SELECT * FROM job WHERE id=?', (job_id,)).fetchone())
    CTX['log'] = job['log']
    assert job['status'] == 'ok', job['log']
    assert CTX['out'].read_text() == SECRET, 'the variable never reached the deploy'
    assert set(db.RUN_DIR.iterdir()) == before, 'the run directory must be left clean'


def the_secret_is_redacted_from_the_log():
    assert SECRET not in CTX['log'], CTX['log']
    assert 'Success' in CTX['log'], CTX['log']


if __name__ == '__main__':
    _setup()
    sys.exit(harness.run(
        an_environment_says_which_tool_plays_it, argv_is_built_from_the_same_columns,
        a_new_host_key_is_never_trusted_on_sight, stored_fields_cannot_become_raw_argv,
        a_missing_file_is_named_rather_than_dialled,
        tags_belong_to_ansible_and_are_refused_here,
        project_hosts_become_a_python_inventory, a_variable_pyinfra_cannot_name_is_refused,
        the_data_file_is_readable_only_by_us, a_real_pyinfra_deploy_runs,
        the_secret_is_redacted_from_the_log))
