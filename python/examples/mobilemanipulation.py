#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import with_statement # for python 2.5
__author__ = 'Rosen Diankov'
__copyright__ = 'Copyright (C) 2009-2010 Rosen Diankov (rosen.diankov@gmail.com)'
__license__ = 'Apache License, Version 2.0'

import time,traceback
from openravepy import *
from openravepy.interfaces import BaseManipulation, TaskManipulation
from openravepy.examples import inversereachability,graspplanning,grasping
from numpy import *
import numpy
from itertools import izip

class GraspReachability(metaclass.AutoReloader):
    def __init__(self,robot,target=None,irgmodels=None):
        self.robot = robot
        self.env = robot.GetEnv()
        if irgmodels is None:
            gmodel = grasping.GraspingModel(robot=robot,target=target)
            if not gmodel.load():
                gmodel.autogenerate()
            irmodel = inversereachability.InverseReachabilityModel(robot=robot)
            if not irmodel.load():
                irmodel.autogenerate()
            self.irgmodels = [[irmodel,gmodel]]
        else:
            # check for consistency
            for irmodel,gmodel in irgmodels:
                assert irmodel.robot == self.robot and irmodel.robot == gmodel.robot and irmodel.manip == gmodel.manip
            self.irgmodels = irgmodels

    def computeGraspDistribution(self,randomgrasps=False,**kwargs):
        """computes distribution of all grasps"""
        densityfns = []
        samplerfns = []
        totalbounds = None
        for irmodel,gmodel in self.irgmodels:
            #validgrasps,validindices = gmodel.computeValidGrasps(checkik=False,backupdist=0.01)
            def graspiter():
                for grasp,graspindex in izip(validgrasps,validindices):
                    yield gmodel.getGlobalGraspTransform(grasp,collisionfree=True),(gmodel,graspindex)
            densityfn,samplerfn,bounds = irmodel.computeAggregateBaseDistribution(graspiter(),**kwargs)
            densityfns.append(densityfn)
            samplerfns.append(samplerfns)
            if totalbounds is None:
                totalbounds = bounds
            else:
                bounds = array((numpy.minimum(bounds[0,:],totalbounds[0,:]),numpy.maximum(bounds[1,:],totalbounds[1,:])))
        def totaldensityfn(**kwargs):
            res = None
            for densityfn in densityfns:
                s = densityfn(**kwargs)
                res = res+s if res is not None else s
            return res
        def totalsamplerfn(**kwargs):
            return samplerfns[random.randint(len(samplerfns))](**kwargs)
        return totaldensityfn,totalsamplerfn,totalbounds
    def sampleGoals(self,samplerfn,N=1,updateenv=False,timeout=inf):
        """samples a base placement and attemps to find an IK solution there.
        samplerfn should return a robot position and an index into gmodel.grasps"""
        goals = []
        numfailures = 0
        starttime = time.time()
        with self.robot:
            while len(goals) < N:
                if time.time()-starttime > timeout:
                    return goals,numfailures
                poses,graspindices,jointstate = samplerfn(N-len(goals))
                for pose,graspindex in izip(poses,graspindices):
                    gmodel = graspindex[0]
                    self.robot.SetTransform(pose)
                    self.robot.SetJointValues(*jointstate)
                    if updateenv:
                        self.env.UpdatePublishedBodies()
                    # before recording failure, validate that base is not in collision
                    if not gmodel.manip.CheckIndependentCollision(CollisionReport()):
                        grasp = gmodel.grasps[graspindex[1]]
                        gmodel.setPreshape(grasp)
                        q = gmodel.manip.FindIKSolution(gmodel.getGlobalGraspTransform(grasp,collisionfree=True),envcheck=True)
                        if q is not None:
                            values = self.robot.GetJointValues()
                            values[gmodel.manip.GetArmJoints()] = q
                            goals.append((grasp,pose,values))
                        elif gmodel.manip.FindIKSolution(gmodel.getGlobalGraspTransform(grasp,collisionfree=True),envcheck=False) is None:
                            numfailures += 1
        return goals,numfailures
    def sampleValidPlacementIterator(self,giveuptime=inf,randomgrasps=False,updateenv=False,randomplacement=False,**kwargs):
        """continues to sample valid goal placements. Returns the robot base position, configuration, and the target grasp from gmodel. Environment should be locked, robot state is saved"""
        starttime = time.time()
        statesaver = self.robot.CreateRobotStateSaver()
        try:
            baseIterators = []
            for irmodel,gmodel in self.irgmodels:
                def graspiter():
                    for grasp,graspindex in gmodel.validGraspIterator(checkik=False,randomgrasps=randomgrasps,backupdist=0.01):
                        yield gmodel.getGlobalGraspTransform(grasp,collisionfree=True),(gmodel,graspindex)
                baseIterator = irmodel.randomBaseDistributionIterator if randomplacement else irmodel.sampleBaseDistributionIterator
                baseIterators.append((baseIterator(Tgrasps=graspiter(),**kwargs),irmodel,gmodel))
            while True:
                iterindex = random.randint(len(baseIterators))
                baseIterator,irmodel,gmodel = baseIterators[iterindex]
                try:                    
                    pose,graspindex,jointstate = baseIterator.next()
                except planning_error:
                    baseIterators.pop(iterindex)
                    continue
                self.robot.SetTransform(pose)
                self.robot.SetJointValues(*jointstate)
                if updateenv:
                    self.env.UpdatePublishedBodies()
                if not irmodel.manip.CheckIndependentCollision(CollisionReport()):
                    gmodel = graspindex[0]
                    grasp = gmodel.grasps[graspindex[1]]
                    gmodel.setPreshape(grasp)
                    q = irmodel.manip.FindIKSolution(gmodel.getGlobalGraspTransform(grasp,collisionfree=True),envcheck=True)
                    if q is not None:
                        values = self.robot.GetJointValues()
                        values[irmodel.manip.GetArmJoints()] = q
                        statesaver.close()
                        yield pose,values,grasp,graspindex
                        statesaver = self.robot.CreateRobotStateSaver()
                        starttime = time.time()
                    else:
                        if not isinf(giveuptime) and time.time()-starttime > giveuptime:
                            raise planning_error('timed out')
        finally:
            statesaver.close()
    def showBaseDistribution(self,thresh=1.0,zoffset=0.5,**kwargs):
        starttime = time.time()
        densityfn,samplerfn,bounds = self.computeGraspDistribution(**kwargs)
        print 'time to build distribution: %fs'%(time.time()-starttime)
        return inversereachability.InverseReachabilityModel.showBaseDistribution(self.env,densityfn,bounds,zoffset=zoffset,thresh=thresh)
    def testSampling(self,**kwargs):
        """random sample goals on the current environment"""
        with self.env:
            configsampler = self.sampleValidPlacementIterator(**kwargs)
            basemanip = BaseManipulation(self.robot)
            jointvalues = self.robot.GetJointValues()
        with RobotStateSaver(self.robot):
            while True:
                try:
                    with self.env:
                        self.robot.SetJointValues(jointvalues) # reset to original
                        pose,values,grasp,graspindex = configsampler.next()
                        gmodel = graspindex[0]
                        print 'found grasp',gmodel.target.GetName(),graspindex[1]
                        self.robot.SetActiveManipulator(gmodel.manip)
                        self.robot.SetTransform(pose)
                        gmodel.setPreshape(grasp)
                        self.robot.SetJointValues(values)
                        final,traj = basemanip.CloseFingers(execute=False,outputfinal=True)
                        self.robot.SetJointValues(final,gmodel.manip.GetGripperJoints())
                        self.env.UpdatePublishedBodies()
                except planning_error, e:
                    traceback.print_exc(e)
                    continue

class MobileManipulationPlanning(metaclass.AutoReloader):
    def __init__(self,robot,grmodel,switchpatterns=None):
        self.envreal = robot.GetEnv()
        self.env=self.envreal
        self.robot = robot
        self.grmodel = grmodel
        self.switchpatterns = switchpatterns
        with self.envreal:
            self.basemanip = BaseManipulation(self.robot)
            self.taskmanip = TaskManipulation(self.robot)
            self.updir = array((0,0,1))

    def graspAndPlaceObjectMobile(self,showdist=False):
        weight = 1.5
        logllthresh = 0.5
        configsampler = self.grmodel.sampleValidPlacementIterator(weight=weight,logllthresh=logllthresh,randomgrasps=True,randomplacement=False,updateenv=False)
        with self.env:
            pose,values,grasp,graspindex = configsampler.next()
        gmodel = graspindex[0]
        print 'found grasp',gmodel.target.GetName(),graspindex[1]
        self.robot.SetActiveManipulator(gmodel.manip)
        self.robot.SetTransform(pose)
        self.robot.SetJointValues(values)
        gmodel.setPreshape(grasp)
        final,traj = self.basemanip.CloseFingers(execute=False,outputfinal=True)
        self.robot.SetJointValues(final,gmodel.manip.GetGripperJoints())
        
    def moveBase(self,goalpose):
        with self.robot:
            self.robot.SetTransform(goalpose)
            if self.env.CheckCollision(self.robot):
                raise planning_error('goal position in self collision')
        with self.env: # find the boundaries of the environment
            envmin = []
            envmax = []
            for b in self.env.GetBodies():
                ab = b.ComputeAABB()
                envmin.append(ab.pos()-ab.extents())
                envmax.append(ab.pos()+ab.extents())
            abrobot = self.robot.ComputeAABB()
            envmin = numpy.min(array(envmin),0)+abrobot.extents()
            envmax = numpy.max(array(envmax),0)-abrobot.extents()
            bounds = array(((envmin[0],envmin[1],-pi),(envmax[0],envmax[1],pi)))
            self.robot.SetAffineTranslationLimits(envmin,envmax)
            self.robot.SetAffineTranslationMaxVels([0.5,0.5,0.5])
            self.robot.SetAffineRotationAxisMaxVels(ones(4))
            self.robot.SetActiveDOFs([],Robot.DOFAffine.X|Robot.DOFAffine.Y|Robot.DOFAffine.RotationAxis,[0,0,1])

            goal2d = [goalpose[4],goalpose[5],numpy.arctan2(goalpose[3],goalpose[:,0])]
            print 'planning to: ',goal2d
            center = r_[goal2d[0:2],0.2]
            xaxis = 0.5*array((cos(goal2d[2]),sin(goal2d[2]),0))
            yaxis = 0.25*array((-sin(goal2d[2]),cos(goal2d[2]),0))
            self.hgoal = self.env.drawlinelist(transpose(c_[center-xaxis,center+xaxis,center-yaxis,center+yaxis]),linewidth=5.0,colors=array((0,1,0)))
            if self.basemanip.MoveActiveJoints(goal=goal2d,maxiter=3000,steplength=0.1) is None:
                raise planning_error('failed to plan goal position')
        print 'waiting for controller'
        self.robot.WaitForController(0)
            
    def performGraspPlanning(self):
        print 'starting to pick and place random objects'
        while True:
            i = random.randint(len(self.graspables))
            try:
                print 'grasping object %s'%self.graspables[i][0].target.GetName()
#                 if len(self.graspables[i]) == 2:
#                     self.graspables[i].append(GraspReachability(robot=self.robot,gmodel=self.graspables[i][0],irmodel=self.irmodel))
# 
#                 with self.envreal:
#                     self.robot.ReleaseAllGrabbed()
#                 success = self.graspAndPlaceObjectMobile(grmodel=self.graspables[i][2],dests=self.graspables[i][1])
#                 print 'success: ',success
            except e:
                print 'failed to grasp object %s'%self.graspables[i][0].target.GetName()
                print e
def run():
    env = Environment()
    try:
        env.SetViewer('qtcoin')
        env.Load('data/lab1.env.xml')
        robot = env.GetRobots()[0]
        env.UpdatePublishedBodies()
        time.sleep(0.1) # give time for environment to update
        self = MobileManipulationPlanning(robot,randomize=True)
        self.performGraspPlanning()
    finally:
        env.Destroy()

if __name__ == "__main__":
    #run()
    print 'not implemented yet'
