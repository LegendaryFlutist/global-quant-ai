"""Regression checks for interruption-safe, explicit-file Git checkpoints."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]

class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='quant-checkpoint-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        (self.root/'scripts').mkdir()
        for name in ['checkpoint.sh','check_publish.py','resume.sh']:
            shutil.copy2(SOURCE/'scripts'/name,self.root/'scripts'/name)
        for name in ['AGENTS.md','PROJECT_PROGRESS.md','TASKS.md']:
            (self.root/name).write_text('Nonsecret test handoff\n')
        self.run_command('git','init','-b','main')
        self.run_command('git','config','user.name','Checkpoint Test')
        self.run_command('git','config','user.email','test@example.invalid')
        self.run_command('git','add','.')
        self.run_command('git','commit','-m','test baseline')
        self.before=self.run_command('git','rev-parse','HEAD').stdout

    def run_command(self,*args,expected=0):
        result=subprocess.run(args,cwd=self.root,text=True,capture_output=True)
        self.assertEqual(result.returncode,expected,result.stdout+result.stderr)
        return result

    def test_only_selected_work_is_committed(self):
        (self.root/'unit.txt').write_text('verified unit\n')
        (self.root/'other.txt').write_text('pending unrelated work\n')
        (self.root/'PROJECT_PROGRESS.md').write_text('Verified result recorded\n')
        self.run_command('bash','scripts/checkpoint.sh','test: verified unit','unit.txt')
        files=self.run_command('git','show','--pretty=','--name-only','HEAD').stdout.splitlines()
        self.assertIn('unit.txt',files)
        self.assertIn('PROJECT_PROGRESS.md',files)
        self.assertNotIn('other.txt',files)
        self.assertEqual((self.root/'other.txt').read_text(),'pending unrelated work\n')

    def test_unrelated_staged_change_is_rejected(self):
        (self.root/'unit.txt').write_text('verified unit\n')
        (self.root/'other.txt').write_text('someone else\n')
        self.run_command('git','add','other.txt')
        result=self.run_command('bash','scripts/checkpoint.sh','test: reject','unit.txt',expected=1)
        self.assertIn('unrelated staged',result.stderr)
        self.assertEqual(self.run_command('git','rev-parse','HEAD').stdout,self.before)
        self.assertEqual(self.run_command('git','diff','--cached','--name-only').stdout.strip(),'other.txt')

    def test_noop_creates_no_commit(self):
        self.run_command('bash','scripts/checkpoint.sh','test: no changes','AGENTS.md')
        self.assertEqual(self.run_command('git','rev-parse','HEAD').stdout,self.before)

    def test_resume_reads_documents_without_changing_git(self):
        (self.root/'AGENTS.md').write_text('User edit to preserve\n')
        before=self.run_command('git','status','--porcelain').stdout
        result=self.run_command('bash','scripts/resume.sh')
        for name in ['AGENTS.md','PROJECT_PROGRESS.md','TASKS.md']:
            self.assertIn(name,result.stdout)
        self.assertIn('User edit to preserve',result.stdout)
        self.assertEqual(self.run_command('git','status','--porcelain').stdout,before)
        self.assertEqual(self.run_command('git','rev-parse','HEAD').stdout,self.before)

    def test_environment_file_cannot_be_committed(self):
        (self.root/'.env').write_text('TEST_PLACEHOLDER=not-a-credential\n')
        result=self.run_command('bash','scripts/checkpoint.sh','test: must reject','.env',expected=1)
        self.assertIn('BLOCKED: .env',result.stdout)
        self.assertEqual(self.run_command('git','rev-parse','HEAD').stdout,self.before)

if __name__=='__main__':unittest.main()
