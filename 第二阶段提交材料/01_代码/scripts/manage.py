"""Project commands: initialize local config, start MongoDB, serve the site, run tests."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='MongoDB questionnaire: init | mongo | serve | test')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='Create .env with a random stable secret if missing')
    mongo = commands.add_parser('mongo', help='Run a local MongoDB in the foreground')
    mongo.add_argument('--port', type=int, default=27017)
    serve = commands.add_parser('serve', help='Serve both frontend and backend')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    test = commands.add_parser('test', help='Run tests and retain JSON evidence')
    test.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'backend'))
    if args.command == 'init':
        config = ROOT / '.env'
        if config.exists():
            print('.env already exists; preserved without changes.')
            return 0
        config.write_text('MONGO_URI=mongodb://127.0.0.1:27017\nMONGO_DB=questionnaire_stage1\n'
            f'APP_SECRET={secrets.token_urlsafe(48)}\nSESSION_HOURS=24\n', encoding='utf-8')
        print('Created .env with a random secret. Keep the secret stable. Database: questionnaire_stage1.')
        return 0
    if args.command == 'mongo':
        executable = os.getenv('MONGOD_BIN') or shutil.which('mongod')
        if not executable:
            parser.error('mongod not found. Add it to PATH or set MONGOD_BIN.')
        directory = ROOT / 'runtime' / 'mongodb'
        directory.mkdir(parents=True, exist_ok=True)
        print(f'Starting MongoDB on 127.0.0.1:{args.port}; data: {directory}', flush=True)
        try:
            return subprocess.call([executable, '--bind_ip', '127.0.0.1', '--port', str(args.port), '--dbpath', str(directory)])
        except KeyboardInterrupt:
            return 130
    if args.command == 'serve':
        import uvicorn
        print(f'Frontend and API: http://{args.host}:{args.port}/', flush=True)
        print(f'Backend source: {ROOT / "backend" / "app" / "main.py"}', flush=True)
        uvicorn.run('app.main:app', host=args.host, port=args.port)
        return 0
    if args.command == 'test':
        directory=ROOT.parent / '03_测试用例'
        scope='selected' if args.pytest_args else 'full'
        return subprocess.call([sys.executable, '-m', 'pytest', '-v', *args.pytest_args],
            cwd=directory, env={**os.environ, 'SURVEY_TEST_SCOPE': scope})


if __name__ == '__main__':
    raise SystemExit(main())
