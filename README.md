# linktool

把一棵树里的重复文件和链接收拢成一个源加若干链接，再把它们按记录重建回来。主要用在 Windows 与 Android/Termux 之间搬运目录：先在本机把重复项移走，让复制工具只搬唯一的内容，到了目标机再按记录把链接补回来。

## 运行

仓库自带 `uv` 环境：

```
uv sync
uv run linktool --help
```

用系统 Python 时把 `src` 加进导入路径，`xxhash` 需要预先装好：

```
PYTHONPATH=src python -m linktool --help
```

`python -m pip install .` 把 `linktool` 装进 `PATH`，构建过程要联网取 hatchling。需要 Python 3.14 以上。

## 命令

| 命令 | 作用 |
| --- | --- |
| `remove` | 每组重复内容只留一个源，其余路径删除 |
| `stage` | 同上，但把其余路径移进暂存区 |
| `rebuild` | 按状态文件把被移走的路径重建为硬链接、符号链接或副本 |
| `restore` | 把暂存区里的条目移回原位 |
| `fix` | 修复因源被移动而失效的符号链接 |

`remove`、`stage`、`rebuild` 的执行形态是一致的：扫描或读状态，算出计划，无法自主决定的项写进 `<root>/.linktool/plan-<命令>.json` 并返回退出码 3，用户填好 `answer` 后重跑同一条命令才真正执行。计划文件的 `args` 段记着生成它的全部设置，重跑时逐项比对，命令或任何一项设置不同就拒绝；想沿用已填的答案，把 `args` 段改成这次的参数再重跑。

计划文件的 `decisions` 段列出这一轮由规则定下的每个源及其依据，`answers` 段记着已经答过、还在沿用的答案。`remove` 与 `stage` 另有一道总确认：整份计划汇成一题，答 yes 之前磁盘上不会有任何改动，所以这两个命令常规是两轮。

## 跨机流程

源机：

```
linktool stage --root D:/tree --staging H:/stage
```

剩下的内容里每个内容只剩一份，硬链接和符号链接都已移走，复制工具没有可展开的链接。复制工具必须把 `<root>/.linktool/` 一起搬走，目标机的 `rebuild` 全靠它。

源机随后还原，与目标机互不影响：

```
linktool restore --staging H:/stage
```

目标机按记录重建：

```
linktool rebuild --root /storage/emulated/0/tree
```

同一台机器上去重只需要 `remove` 加 `rebuild`。

## 状态与记录

`<root>/.linktool/state.json` 记录每一组的源、每个被移走路径的原形态、mtime 和重建状态；`state.jsonl` 是逐条进度的追加日志，收尾时折回 `state.json`，崩溃后下次启动先折一次。两份文件都随树复制，因此目标机不需要源机在位。

暂存区自己拥有 `<staging>/.linktool/staging.json`，记录被移动路径的原位置。暂存区必须位于受管树之外，否则复制工具会把它一起搬走。

路径在 `state.json` 里记成相对受管树的形式并且用 `/` 分隔，同一份记录在 Windows 的 `D:/tree` 和 Android 的 `/storage/emulated/0/tree` 上都能解析。受管树之外的路径记绝对路径，跨机后由用户重新指定。

## 判断重复

先按大小分组，再对 1 MiB 以上的文件取首尾各 4 KiB 做快速哈希，最后全量 `xxhash.xxh3_128`。最终身份是「大小 + 全量哈希」。符号链接按它解析出的文件参与内容分组，目录链接按解析出的目录分组。

组里的源按两条规则定：组内符号链接解析出的 realpath 唯一时用那个 realpath；否则若偏好目录下恰好一个成员就用它。两条规则都成立却指向不同文件时整组进问题文件。命中偏好目录的组成员是链接时，链接新源指向它的 realpath；同目录下的多个硬链接按一个源对象计数。

两条规则都不成立时的处理由 `pick` 决定：`ask` 逐组提问，`first` 按路径排序取最前。`first` 选出的源和其他规则选出的源一样写进 `decisions` 段，等用户一并确认。

重建形态沿用原形态：原先是文件的按「硬链接 → 符号链接 → 副本」降级，原先是链接的仍然建成链接。硬链接要求源与目标同卷，符号链接与硬链接的支持情况由在目标目录里创建一次探针并按卷缓存来判断。要回退副本时，所有副本的预估总大小先进问题文件，用户确认后才复制。目标卷不支持符号链接时，链接没有可以降级的形态，这些成员连同路径写进问题文件，确认后跳过并保持待重建，其余成员照常重建。

## 平台差异

Windows 上 `os.rename`、`os.replace`、`shutil.move` 作用在链接的目标上，链接本身的移动一律是「读指向，在新位置重建，再删旧的」。同一条道理让 `fix` 无法原子替换链接：新源先写进 `state.json`，再删旧链接、建新链接，中途失败的链接下次 `fix` 用记录补回。

Windows 记录链接自身指向文件还是目录（`st_file_attributes` 的 `FILE_ATTRIBUTE_DIRECTORY` 位），所以失效链接也能正确重建。POSIX 不记录也不需要，`os.symlink` 没有类型参数。

`os.readlink` 在 Windows 上返回带 `\\?\` 前缀的绝对路径，读到的指向先去掉前缀再判断绝对与相对，否则 `isabs` 会把它判成相对路径。

junction 按目录链接处理，重建时变成目录符号链接，转换时打一行警告。`os.path.islink` 认不出 junction，扫描时用 `os.path.isjunction` 一并识别，否则递归会钻进 junction 里。

`os.utime(..., follow_symlinks=False)` 在 Windows 上抛 `NotImplementedError`，链接自身的 mtime 只能保留在 POSIX 上，Windows 上输出警告。硬链接与源共享 inode，它的 mtime 就是源的 mtime，无法单独设置。

## 参数

`--root` 是状态文件的锚点，决定记录里相对路径的基准。`--scan` 默认就是 `root`；`--prefer` 列出源偏好目录。这两个参数一次可以带多个值（`--scan A B`），也可以重复出现（`--scan A --scan B`），两种写法的结果相同。`--pick` 取 `ask`（逐组提问）或 `first`（按路径排序取最前），默认 `ask`。

`--base-dir` 只决定链接指向写成相对还是绝对，默认等于 `root`，可以设为 `root` 的上级，让两棵独立的树之间也能用相对路径。`--rel-mode` 取 `base`（只在 `base_dir` 内转相对）、`all`（全部尝试）或 `off`（保留绝对）。

每条命令只接受自己读得到的参数：`stage` 与 `restore` 需要 `--staging`，`rebuild` 与 `fix` 接受 `--base-dir` 与 `--rel-mode`，`fix` 只扫链接不选源，所以没有 `--prefer` 与 `--pick`。重跑时要给出的设置保持一致，写进脚本比每次重新敲一遍省事。

## 验证

```
uv run ruff format --check
uv run ruff check
uv run pyright
uv run pytest
```
