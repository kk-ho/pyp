#-*-coding:utf-8 -*-
from __future__ import division
'''
coder:mars.ho
coding date:2017-03-14
scope: oracle  expdp to impdp
limition: asm to asm
function: 一键实现逻辑数据迁移。
description:
    v1.0：
    通过函数接收源、目的库的信息。
    v1.1:
    将源、目的库的信息，放在配置文件上，程序初始化时自动读取。
    2017-03-14 第一轮测试已经通过

'''
import cx_Oracle
import ConfigParser
from os import system

class ora_datamove(object):
    def __init__(self):
        f1 = ConfigParser.ConfigParser()
        f1.readfp(open("migration.conf", mode='r+'))
        self.source_ip=f1.get("source", "IP")
        self.source_port = f1.get("source", "port")
        self.source_service=f1.get("source", "service")
        self.source_username=f1.get("source", "username")
        self.source_password=f1.get("source", "password")
        self.source_schemaname=f1.get("source", "schemaname").upper()
        self.source_tnsname=f1.get("source", "tnsnames").upper()
        self.source_logdir="DATA_PUMP_DIR"
        self.dumpfile = self.source_schemaname + ".dmp"

        self.datafile=[]   #存储元组，每个元组的元素tablespace_name,文件数，文件大小

        self.target_ip = f1.get("target", "IP")
        self.target_port = f1.getint("target", "port")
        self.target_service = f1.get("target", "service")
        self.target_username = f1.get("target", "username")
        self.target_password = f1.get("target", "password")
        self.target_schemaname = f1.get("target", "schemaname").upper()
        self.target_tnsname = f1.get("target", "tnsnames").upper()
        self.target_logdir = "DATA_PUMP_DIR"

        #self.remap_schemaname = f1.get("imp", "remap_schemaname")
        #self.remap_tablespace= f1.get("imp", "remap_tablespaces")
        self.remap_tablespace =""
        self.remap_schemaname=""

        self.schema_space=0
        self.diskgroup_space=0



    def check_source(self):
        #检查要导出的schema大小（data and index）
        #检查存放的目录是否有足够的空间容纳
        print("开始检查源库环境....".decode("utf-8"))
        source_db=cx_Oracle.connect(self.source_username,self.source_password,self.source_tnsname)
        source_cursor=source_db.cursor()
        #获取导出schema对应多少个表空间，以及每个表空间的大小
        sql="select distinct tablespace_name from dba_segments where owner='"+self.source_schemaname.upper()+"'"
        source_cursor.execute(sql)
        rowset=source_cursor.fetchall()
        for i in range(len(rowset)):
            #self.source_tablespace.append(rowset[i][0])
            sql='''select tablespace_name,count(*) file_count,round(avg(bytes)/1024/1024,2) file_size
                    from dba_data_files where tablespace_name=:tb_name
                    group by tablespace_name
            '''
            source_cursor.prepare(sql)
            source_cursor.execute(None,{"tb_name":rowset[i][0]})
            tb_rowset=source_cursor.fetchall()
            self.datafile.append(tb_rowset[0])
        print("表空间大小%s".decode("utf-8")%(self.datafile))
        #print(self.source_tablespace)

        source_cursor.prepare("select round(sum(bytes)/1024/1024,2) space from dba_segments where owner=:owner")
        source_cursor.execute(None,{"owner":self.source_schemaname})  #预估导出dump文件的大小使用绑定变量
        row_set=source_cursor.fetchall()
        self.schema_space=row_set[0][0]


        source_cursor.execute("select * from (select name,free_mb from v$asm_diskgroup  order by free_mb desc) where rownum=1")
        rowset = source_cursor.fetchall()
        self.diskgroup_space=rowset[0][1]


        print("预计导出的dump文件大小为：".decode("utf-8")+str(self.schema_space)+"mb")
        #print(""+self.diskgroup_space)

        if round(self.diskgroup_space/self.schema_space)>=2:
            #创建ora目录给expdp使用
            sql = "select name,free_mb from v$asm_diskgroup where free_mb>=:free_mb order by free_mb DESC"
            source_cursor.prepare(sql)
            source_cursor.execute(None, {"free_mb": self.schema_space})
            rowset = source_cursor.fetchall()
            print("请选择保存目标dump文件的磁盘组:".decode("utf-8"))
            diskgroup_name = []
            for row in rowset:
                print(row[0] + "     " + str(row[1]))
                diskgroup_name.append(row[0])
            dump_diskgroup = raw_input("Please input the diskgroup name:").strip()
            try:
                if diskgroup_name.index(dump_diskgroup.upper()) >= 0:
                    pass
            except Exception as e:
                print(e)
                print("你没有输入正确的磁盘组名字，程序退出！".decode("utf-8"))
                exit()
            # 创建目录
            sql = "create directory expdir as '+" + dump_diskgroup + "'"
            try:
                source_cursor.execute(sql)
            except Exception as e:
                print(e)
            source_db.close()
            print("源库检查完成!".decode("utf-8"))
            return 1
        else:
            source_db.close()
            return 0

    def check_target(self):
        #检查当前的DB是否有足的空间创建新的表空间和schema
        #检查当前的目录是否有足够的空间
        #先判断是否有同样的schema名称和tablespace名称,如有，退出并提示先做删除处理
        print("开始检查目的库环境....".decode("utf-8"))
        target_db = cx_Oracle.connect(self.target_username, self.target_password, self.target_tnsname)
        target_cursor = target_db.cursor()
        print("检查是否有同名的schema....".decode("utf-8"))
        sql="select count(*) from dba_users where username='"+self.source_schemaname+"'"
        #print(sql)
        target_cursor.execute(sql)
        rowset=target_cursor.fetchall()
        is_username=rowset[0][0]
        #print(rowset[0][0])
        if is_username!=0 :
            print("即将导入的schema已经存在！".decode("utf-8"))
            return 0

        #判断目的库是否有同名的表空间
        print("检查是否有同名的tablespace....".decode("utf-8"))
        self.new_tbset=[]
        for i in range(len(self.datafile)):
            sql="select count(*) from dba_tablespaces where tablespace_name='"+self.datafile[i][0]+"'"
            target_cursor.execute(sql)
            rowset=target_cursor.fetchall()
            is_tablespace=rowset[0][0]
            if is_tablespace!=0 :
                print("即将导入的tablespace已经存在！".decode("utf-8"))
                new_tb_name=raw_input("please input the new name for tablespace:")
                self.new_tbset.append((new_tb_name,self.datafile[i][1],self.datafile[i][2]))
            else:
                self.new_tbset.append(self.datafile[i])

        sql="select name,free_mb from v$asm_diskgroup where free_mb>=:free_mb order by free_mb DESC"
        target_cursor.prepare(sql)
        target_cursor.execute(None,{"free_mb":self.schema_space})
        rowset=target_cursor.fetchall()
        print("请选择保存目标dump文件的磁盘组:".decode("utf-8"))
        diskgroup_name=[]
        for row in rowset:
            print(row[0]+"     "+str(row[1]))
            diskgroup_name.append(row[0])
        dump_diskgroup=raw_input("Please input the diskgroup name:").strip()
        try:
            if diskgroup_name.index(dump_diskgroup.upper()) >1 :
                pass
        except Exception as e:
            print(e)
            print("你没有输入正确的磁盘组名字，程序退出！".decode("utf-8"))
            exit()


        print("请选择创建%s表空间的磁盘组".decode("utf-8")%(self.source_schemaname))
        for row in rowset:
            print(row[0] + "    " + str(row[1]))
            diskgroup_name.append(row[0])
        tablespace_diskgroup = raw_input("Please input the diskgroup name:").strip()
        try:
            if diskgroup_name.index(tablespace_diskgroup.upper()) > 1:
                pass
        except Exception as e:
            print(e)
            print("你没有输入正确的磁盘组名字，程序退出！".decode("utf-8"))
            exit()

        #创建目录
        sql="create directory impdir as '+"+dump_diskgroup+"'"
        target_cursor.execute(sql)

        #创建表空间，计算创建多大的表空间，是schema_space的1.5倍，并且平均分配到24G文件上。
        sql=""
        for i in range(len(self.new_tbset)):

            for j in range(self.new_tbset[i][1]):
                if j==0:
                    sql = "create tablespace " + self.new_tbset[i][0] + "  datafile '+" + tablespace_diskgroup + "' size " + str(self.new_tbset[i][2]) + "m"
                else:
                    sql=sql + ",'+"+tablespace_diskgroup +"' size " + str(self.new_tbset[i][2]) + "m"
            print(sql)
            target_cursor.execute(sql)
        #导入的数据无须创建schema用户
        print("成功创建导入的目录和表空间！".decode("utf-8"))
        return 1

    #导出数据库
    def expdp(self):
        cmd="expdp "+self.source_username+"/"+self.source_password+"@"+self.source_tnsname+" schemas="+self.source_schemaname
        cmd=cmd+" dumpfile="+self.source_schemaname+".dmp"+" logfile="+self.source_logdir+":"+self.source_schemaname+".log directory=expdir"
        #print(cmd)
        try:
            system(cmd)
        except Exception as e:
            print(e)
        print("导出成功".decode("utf-8"))

    def impdp(self):
        print(self.datafile)
        print(self.new_tbset)
        for i in range(len(self.datafile)):
            self.remap_tablespace=self.remap_tablespace+self.datafile[i][0]+":"+self.new_tbset[i][0]
        cmd="impdp "+self.target_username+"/"+self.target_password+"@"+self.target_tnsname
        cmd=cmd+" directory=impdir  logfile=DATA_PUMP_DIR:"+self.source_schemaname+".log dumpfile="+self.source_schemaname+".dmp"
        cmd=cmd+" schemas="+self.source_schemaname+"  remap_schema="+self.source_schemaname+":"+self.target_schemaname
        cmd=cmd+" remap_tablespace="+self.remap_tablespace
        system(cmd)
        #print(cmd)


    def create_dblink(self):
        #在源库上创建dblink ，验证dblink有效，并进行文件传输
        sql="create database link transfer_link connect to "+self.target_username+" identified by " +self.target_password
        sql=sql+" using '(DESCRIPTION= (FAILOVER=on) (LOAD_BALANCE=on) (ADDRESS= (PROTOCOL=TCP) (HOST="
        sql=sql+self.target_ip+") (PORT=1521)) (CONNECT_DATA= (SERVICE_NAME="+self.target_service+")))'"

        source_db = cx_Oracle.connect(self.source_username, self.source_password, self.source_tnsname)
        source_cursor=source_db.cursor()
        try:
            print(sql)
            source_cursor.execute(sql)
            source_cursor.execute("select count(*) from  all_tables@transfer_link")
            rowset=source_cursor.fetchall()
            if rowset[0][0] >0 :
                print("create dblink successful!")
                sql="call SYS.DBMS_FILE_TRANSFER.PUT_FILE('expdir', '"+self.dumpfile+"','impdir','"+self.dumpfile+"','transfer_link')"
                print(sql)
                source_cursor.execute(sql)
        except Exception as e:
            print(e)
        source_cursor.execute("drop database link transfer_link")


    def datamove(self):
        a=self.check_source()
        b=self.check_target()
        if (a==1) and (b==1) :
             #start datamove
            self.expdp()
            self.create_dblink()
            self.impdp()

    def close(self):
        # 删除dblink,directory
        try:
            source_db = cx_Oracle.connect(self.source_username, self.source_password, self.source_tnsname)
            source_cursor = source_db.cursor()
            source_cursor.execute("drop directory expdir")
            source_cursor.close()
            source_db.close()
        except Exception as e:
            print(e)

        try:
            target_db = cx_Oracle.connect(self.target_username, self.target_password, self.target_tnsname)
            target_cursor = target_db.cursor()
            target_cursor.execute("drop directory impdir")
            target_cursor.close()
            target_db.close()
        except Exception as e:
            print(e)
        print("为免造成空间浪费，请自行清理dmp文件！".decode("UTF-8"))

if __name__=="__main__" :
    odm=ora_datamove()
    #odm.create_dblink()
    odm.datamove()
    #odm.check_source()
    #odm.check_target()
    #odm.impdp()
    #odm.check_source()
    #odm.expdp()
    odm.close()
    #print(odm.check_source())
    #print(odm.check_target())



