"""Local administration only: no public admin password or database credentials."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
import sqlite3
import sys

from .service import AuthService, username
from .settings import Settings


def expiry(value):
    if value.lower() == "never":
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("到期时间须包含时区，例如 2027-01-01T00:00:00+08:00；never 表示永久")
    return int(parsed.timestamp())


def read_password():
    password = getpass.getpass("新密码（至少 12 位，不显示）：")
    if password != getpass.getpass("再输入一次："):
        raise ValueError("两次密码不一致")
    return password


def fmt_date(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Studio 登录服务账号管理；请在服务目录运行")
    parser.add_argument("--db", help="数据库绝对路径；默认读取 STUDIO_AUTH_DB 或 data/auth.sqlite3")
    cmds = parser.add_subparsers(dest="command", required=True)
    cmds.add_parser("init", help="初始化数据库，不创建默认账号")
    for command in ('add-admin', 'password-admin'):
        sub = cmds.add_parser(command, help='创建或重置网页后台管理员')
        sub.add_argument('username')
    add = cmds.add_parser("add", help="创建账号，交互输入密码")
    add.add_argument("username")
    add.add_argument("--expires", required=True, help="带时区的 ISO 日期，或 never")
    add.add_argument("--max-devices", type=int, default=0, help="0=不限登录设备")
    cmds.add_parser("list", help="列出账号，不显示密码哈希")
    update = cmds.add_parser("set", help="修改账号期限、开关、设备数")
    update.add_argument("username")
    update.add_argument("--enabled", choices=["yes", "no"])
    update.add_argument("--expires")
    update.add_argument("--max-devices", type=int)
    for command in ("password", "revoke", "sessions"):
        sub = cmds.add_parser(command)
        sub.add_argument("username")
    backup = cmds.add_parser("backup", help="生成一致性备份，拒绝覆盖已有文件")
    backup.add_argument("target")
    cmds.add_parser("prune", help="清理过期会话、限流计数和 90 天以前的审计记录")
    version_check = cmds.add_parser("client-version-check", help="暂停/恢复所有客户端体系的版本校验，保留登录和账号授权")
    version_check.add_argument("action", choices=["status", "pause", "resume"])
    args = parser.parse_args()
    if args.db:
        os.environ["STUDIO_AUTH_DB"] = args.db
    settings = Settings.from_env()
    if args.command == "client-version-check" and not settings.database.is_file():
        raise ValueError("数据库不存在，请用 --db 指定正在使用的数据库；未创建新数据库")
    service = AuthService(settings)
    command = args.command
    if command == "client-version-check":
        from .store import client_version_check_enabled
        if args.action != "status":
            service.store.set_client_version_check(args.action == "resume", "local-cli", service.now())
        with service.store.connect() as db:
            enabled = client_version_check_enabled(db)
        print("数据库：", service.store.path)
        print("版本校验：" + ("已开启" if enabled else "已暂停；账号登录、有效期和 Key 授权照常检查"))
    elif command in ('add-admin', 'password-admin'):
        from .admin import set_admin
        set_admin(service, args.username, read_password(), create=command == 'add-admin')
        print('管理员已保存，旧管理会话已撤销；请打开域名 /admin/')
    elif command == "init":
        print("数据库已初始化：", service.store.path)
    elif command == "add":
        end = expiry(args.expires)
        service.create_user(args.username, read_password(), end, args.max_devices)
        print("账号已创建：", username(args.username))
    elif command == "list":
        with service.store.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT id,username,enabled,expires_at,max_devices,created_at "
                                               "FROM users ORDER BY username")]
        for row in rows:
            row["expires_at"] = fmt_date(row["expires_at"])
            row["created_at"] = fmt_date(row["created_at"])
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif command == "set":
        if args.enabled is None and args.expires is None and args.max_devices is None:
            raise ValueError("至少指定一项要修改的设置")
        service.manage_user(args.username, enabled=None if args.enabled is None else args.enabled == "yes",
                            expires_at=expiry(args.expires) if args.expires else "unchanged",
                            max_devices=args.max_devices)
        print("账号设置已更新；停用或调整设备上限会撤销当前登录")
    elif command == "password":
        service.manage_user(args.username, password=read_password())
        print("密码已更新，旧登录全部撤销")
    elif command == "revoke":
        service.manage_user(args.username, revoke=True)
        print("该账号的全部登录已撤销")
    elif command == "sessions":
        with service.store.connect() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT s.id,s.device_name,s.created_at,s.last_seen_at,s.expires_at,s.revoked_at "
                "FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.username=? ORDER BY s.created_at DESC",
                (username(args.username),))]
        for row in rows:
            for key in ("created_at", "last_seen_at", "expires_at", "revoked_at"):
                row[key] = fmt_date(row[key])
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif command == "backup":
        service.store.backup(args.target)
        print("备份完成：", args.target)
    elif command == "prune":
        service.prune()
        print("清理完成")


if __name__ == "__main__":
    try:
        main()
    except sqlite3.IntegrityError:
        print("操作失败：账号已存在或数据库约束不满足", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
