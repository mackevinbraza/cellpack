# -*- coding: utf-8 -*-
# autoPACK Authors: Graham T. Johnson, Mostafa Al-Alusi, Ludovic Autin, Michel Sanner
#   Based on COFFEE Script developed by Graham Johnson between 2005 and 2010
#   with assistance from Mostafa Al-Alusi in 2009 and periodic input
#   from Arthur Olson's Molecular Graphics Lab
#
# Compartment.py Authors: Graham Johnson & Michel Sanner with editing/enhancement from Ludovic Autin
#
# Translation to Python initiated March 1, 2010 by Michel Sanner with Graham Johnson
#
# Class restructuring and organization: Michel Sanner
#
# Copyright: Graham Johnson ©2010
#
# This file "Compartment.py" is part of autoPACK, cellPACK.
#
#    autoPACK is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    autoPACK is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with autoPACK (See "CopyingGNUGPL" in the installation.
#    If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
# @author: Graham Johnson, Ludovic Autin, & Michel Sanner

# Hybrid version merged from Graham's Sept 6, 2011 and Ludo's April 2012
# version on May 16, 2012, re-merged on July 5, 2012 with thesis versions

# Hybrid version merged from Graham's Sept 2011 and Ludo's April 2012 version on May 16, 2012
# Updated with Sept 16, 2011 thesis versions on July 5, 2012

# TODO: Describe Organelle class here at high level

# TODO: Graham and Ludovic implemented a 2D density function to obtain target numbers for
#   filling surfaces.  This should be formalized and named something other than molarity
#   or molarity should be converted to a 2D value behind the scenes.
# IDEA: We should offer the user an option to override molarity with a specific
#   number, e.g., "I want to place 3 1xyz.pdb files in compartment A" rather than
#   forcing them to calculate- "I need to place 0.00071M of 1xyz.pdb to get 3 of them
#   in an compartment A of volume=V."

# IDEAS

# randomly select recipe and then randomly select free point in set of free
# points corresponding to this recipe would allow giving surface more
# chances to get filled

# NOTE changing smallest molecule radius changes grid spacing and invalidates
#      arrays saved to file
import logging
import os
import pickle

import numpy
from time import time
import math
from trimesh.voxel import creation
from scipy import spatial

import cellpack.autopack as autopack
from cellpack.autopack import transformation as tr, binvox_rw
from cellpack.autopack.BaseGrid import gridPoint
from .Recipe import Recipe
from .ray import (
    makeMarchingCube,
    vcross,
    vlen,
    findPointsCenter,
    f_ray_intersect_polyhedron,
    vdiff,
)

try:
    import panda3d
    from panda3d.core import Mat3, Mat4, Point3, TransformState, BitMask32
    from panda3d.bullet import BulletCapsuleShape, BulletRigidBodyNode
except Exception as e:
    panda3d = None
    print("Failed to get Panda ", e)

helper = autopack.helper
AFDIR = autopack.__path__[0]


class CompartmentList:
    """
    The CompartmentList class
    ==========================
    Handle a list of compartments.
    """

    def __init__(self):
        self.log = logging.getLogger("compartment")
        self.log.propagate = False

        # list of compartments inside this compartment
        self.compartments = []

        # point to parent compartment or Environment
        self.parent = None

    def _add_compartment(self, compartment):
        """add a new compartment to the list"""
        assert compartment.parent is None
        assert isinstance(compartment, Compartment)
        self.compartments.append(compartment)
        compartment.parent = self


class Compartment(CompartmentList):
    """
    The Compartment class
    ==========================
    This class represents a sub volume delimited by a polyhedral
    surface. Compartment can be nested
    """

    def __init__(self, name, object_info):
        super().__init__()
        self.name = name
        self.center = [0, 0, 0]  # calculated centroid of the mesh
        self.position = [0, 0, 0]  # where the object is placed
        self.vertices = None
        self.faces = None
        self.vnormals = None
        self.scale = 1.0
        self.fnormals = None
        self.gname = name
        self.filename = None
        self.ref_obj = None
        self.meshType = None
        self.representations = object_info["representations"]
        if self.representations.has_mesh():
            self.gname = self.representations.get_mesh_name()
            self.meshType = self.representations.get_mesh_format()
            self.filename = self.representations.get_mesh_path()
            self.path = autopack.fixOnePath(self.filename)
        self.stype = "mesh"
        self.radius = object_info["radius"] if "radius" in object_info else 200.0
        self.height = 0.0
        self.axis = [0, 1, 0]
        self.area = 0.0
        self.mesh = None
        self.rbnode = None
        self.ghost = False
        self.bb = None
        self.diag = 9999.9
        self.ghost = None
        self.encapsulating_radius = (
            object_info["radius"] if "radius" in object_info else 200.0
        )
        self.checkinside = True
        self.innerRecipe = None
        self.surfaceRecipe = None
        self.surfaceVolume = 0.0
        self.interiorVolume = 0.0
        self.rapid_model = None
        # list of grid point indices inside organelle
        self.insidePoints = None
        # list of grid point indices on compartment surface
        self.surfacePoints = None
        self.surfacePointsNormals = {}  # will be point index:normal

        self.number = None  # will be set to an integer when this compartment
        # is added to a Environment. Positivefor surface pts
        # negative for interior points
        # self.parent = None
        self.molecules = []
        # list of ( (x,y,z), rotation, ingredient) triplet generated by fill
        self.overwriteSurfacePts = True
        # do we discretize surface point per edges
        self.highresVertices = None
        # if a highres vertices is provided this give the surface point,
        # not the one provides
        # to compute inside points.
        self.is_sphere = object_info["type"] == "single_sphere"
        self.type = object_info["type"]
        self.is_box = (
            "bounding_box" in object_info and object_info["bounding_box"] is not None
        )
        self.bounding_box = (
            object_info["bounding_box"] if "bounding_box" in object_info else None
        )
        self.is_orthogonal_bounding_box = 1 if "bounding_box" in object_info else None

        self.grid_type = "regular"
        self.grid_distances = None  # signed closest distance for each point
        # TODO Add openVDB
        # if self.filename is None:
        #     autopack.helper.saveDejaVuMesh(
        #         autopack.cache_geoms + os.sep + self.name, self.vertices, self.faces
        #     )
        #     self.filename = autopack.cache_geoms + os.sep + self.name
        #     self.ref_obj = self.name

    def reset(self):
        """reset the inner compartment data, surface and inner points"""
        # list of grid point indices inside compartment
        self.insidePoints = None
        # list of grid point indices on compartment surface
        self.surfacePoints = None
        self.surfacePointsNormals = {}  # will be point index:normal
        # self.molecules = []

    def transformMesh(self, pos, rotation):
        rot = tr.matrix_from_quaternion(rotation).transpose()
        m = numpy.identity(4)
        m[:3, :3] = rot[:3, :3]
        m[3, :3] = pos
        self.vertices = autopack.helper.ApplyMatrix(self.vertices, m.transpose())
        # Recompute the normal ?
        # self.vnormals = autopack.helper.ApplyMatrix(self.vnormals,m.transpose())
        self.vnormals = self.getVertexNormals(self.vertices, self.faces)
        # self.vnormals = autopack.helper.normal_array(self.vertices,numpy.array(self.faces))
        self.center = pos

    def buildSphere(self, mesh_store):
        geom = None
        geom = mesh_store.create_sphere(self.gname, 4, self.radius)
        self.faces, self.vertices, self.vnormals = mesh_store.decompose_mesh(
            geom, edit=False, copy=False, tri=True
        )

    def buildMesh(self, data, mesh_store):
        """
        Create a polygon mesh object from a dictionary verts,faces,normals
        """
        geom, vertices, faces, vnormals = mesh_store.build_mesh(data, self.gname)
        self.vertices = vertices
        self.faces = faces
        self.vnormals = vnormals
        self.filename = autopack.cache_geoms + os.sep + self.gname
        self.ref_obj = self.name
        return geom

    def _get_volume(self, mesh_store):
        if self.is_sphere:
            return 4 * math.pi * self.radius**3 / 3

    def initialize_shape(self, mesh_store):
        if self.is_sphere:
            # one sphere, geom is a dictionary
            self.buildSphere(mesh_store)
        if self.vertices is None and self.type == "mesh":
            self.faces, self.vertices, self.vnormals = self.getMesh(mesh_store)
            self.ref_obj = self.name
        if self.meshType == "raw":
            # need to build the mesh from v,f,n
            self.buildMesh(self.meshFile, mesh_store)
        if self.type == "mb":
            # one sphere, geom is a dictionary
            self.buildSphere(mesh_store)
        if self.vertices is not None and len(self.vertices):
            # can be dae/fbx file, object name that have to be in the scene or dejaVu indexedpolygon file
            self.bb = self.getBoundingBox()
            if not self.is_sphere:
                center, radius = mesh_store.get_nsphere(self.gname)
                self.center = center
                self.encapsulating_radius = radius
                self.radius = mesh_store.get_smallest_radius(self.gname, center)

    def addShapeRB(self):
        # in case our shape is a regular primitive
        if self.stype == "capsule":
            shape = BulletCapsuleShape(self.radius, self.height, self.axis)
        else:
            shape = self.addMeshRB()
        inodenp = self.parent.worldNP.attachNewNode(BulletRigidBodyNode(self.name))
        inodenp.node().setMass(1.0)
        inodenp.node().addShape(
            shape, TransformState.makePos(Point3(0, 0, 0))
        )  # rotation ?

        inodenp.setCollideMask(BitMask32.allOn())
        inodenp.node().setAngularDamping(1.0)
        inodenp.node().setLinearDamping(1.0)
        self.parent.world.attachRigidBody(inodenp.node())
        inodenp = inodenp.node()
        return inodenp

    def setGeomFaces(self, tris, face):
        # have to add vertices one by one since they are not in order
        if len(face) == 2:
            face = numpy.array([face[0], face[1], face[1], face[1]], dtype="int")
        for i in face:
            tris.addVertex(i)
        tris.closePrimitive()

    #        #form = GeomVertexFormat.getV3()
    #        form = GeomVertexArrayFormat.getV3()
    #        vdata = GeomVertexData("vertices", form, Geom.UHDynamic)#UHStatic)
    #
    #        vdatastring = npdata.tostring()
    #        vdata = GeomVertexArrayData ("vertices", form, Geom.UHStatic)
    #        vdata.modifyArray(0).modifyHandle().setData(vdatastring)
    #
    #        pts = GeomPoints(Geom.UHStatic)
    #        geomFaceNumpyData = numpy.array(range(count),dtype=numpy.uint32)
    #        #add some data to face-array
    #        pts.setIndexType(GeomEnums.NTUint32)
    #        faceDataString = geomFaceNumpyData.tostring()
    #        geomFacesDataArray = pts.modifyVertices()
    #        geomFacesDataArray.modifyHandle().setData(faceDataString)
    #        pts.setVertices(geomFacesDataArray)
    #

    def addMeshRB(self):
        from panda3d.core import GeomEnums
        from panda3d.core import (
            GeomVertexFormat,
            GeomVertexData,
            Geom,
            GeomTriangles,
        )
        from panda3d.bullet import (
            BulletTriangleMesh,
            BulletTriangleMeshShape,
        )

        # step 1) create GeomVertexData and add vertex information

        # can do it from numpy array directly...should be faster
        format = GeomVertexFormat.getV3()
        vdata = GeomVertexData("vertices", format, Geom.UHStatic)
        vdatastring = self.vertices.tostring()
        vdata.modifyArray(0).modifyHandle().setData(vdatastring)

        # vertexWriter=GeomVertexWriter(vdata, "vertex")
        # [vertexWriter.addData3f(v[0],v[1],v[2]) for v in self.vertices]

        # step 2) make primitives and assign vertices to them
        tris = GeomTriangles(Geom.UHStatic)
        geomFaceNumpyData = numpy.array(self.faces, dtype=numpy.uint32)
        # add some data to face-array
        tris.setIndexType(GeomEnums.NTUint32)
        faceDataString = geomFaceNumpyData.tostring()
        geomFacesDataArray = tris.modifyVertices()
        geomFacesDataArray.modifyHandle().setData(faceDataString)
        tris.setVertices(geomFacesDataArray)

        # [self.setGeomFaces(tris,face) for face in self.faces]

        # step 3) make a Geom object to hold the primitives
        geom = Geom(vdata)
        geom.addPrimitive(tris)
        # step 4) create the bullet mesh and node
        # if ingr.convex_hull:
        #     shape = BulletConvexHullShape()
        #     shape.add_geom(geom)
        # else :
        mesh = BulletTriangleMesh()
        mesh.addGeom(geom)
        shape = BulletTriangleMeshShape(mesh, dynamic=False)  # BulletConvexHullShape
        self.log.info("shape ok %r", shape)
        # inodenp = self.worldNP.attachNewNode(BulletRigidBodyNode(ingr.name))
        # inodenp.node().setMass(1.0)
        #        inodenp.node().addShape(shape)#,TransformState.makePos(Point3(0, 0, 0)))#, pMat)#TransformState.makePos(Point3(jtrans[0],jtrans[1],jtrans[2])))#rotation ?
        return shape

    def create_rbnode(self):
        # Sphere
        if panda3d is None:
            return None
        trans = [0, 0, 0]
        rotMat = mat = numpy.identity(4)
        mat = mat.transpose().reshape((16,))
        mat3x3 = Mat3(
            mat[0], mat[1], mat[2], mat[4], mat[5], mat[6], mat[8], mat[9], mat[10]
        )
        pmat = Mat4(
            mat[0],
            mat[1],
            mat[2],
            mat[3],
            mat[4],
            mat[5],
            mat[6],
            mat[7],
            mat[8],
            mat[9],
            mat[10],
            mat[11],
            trans[0],
            trans[1],
            trans[2],
            mat[15],
        )
        pMat = TransformState.makeMat(pmat)
        if self.parent.panda_solver == "ode":
            pMat = mat3x3
        inodenp = self.addMeshRB(pMat, trans, rotMat)
        if self.panda_solver == "bullet":
            inodenp.setCollideMask(BitMask32.allOn())
            inodenp.node().setAngularDamping(1.0)
            inodenp.node().setLinearDamping(1.0)
            inodenp.setMat(pmat)
            self.parent.world.attachRigidBody(inodenp.node())
            inodenp = inodenp.node()
        elif self.panda_solver == "ode":
            inodenp.setCollideBits(BitMask32(0x00000002))
            inodenp.setCategoryBits(BitMask32(0x00000001))
            # boxGeom.setBody(boxBody)
        self.parent.rb_panda.append(inodenp)
        # self.moveRBnode(inodenp.node(), trans, rotMat)
        return inodenp

    def get_rb_model(self):
        if self.rbnode is None:
            self.rbnode = self.create_rbnode()
        return self.rbnode

    def getMesh(self, mesh_store):
        """
        Retrieve the compartment 3d representation from the given filename

        @type  filename: string
        @param filename: the name of the input file
        @type  rep: string
        @param rep: the name of the input file for the representation
        """
        geometry = None
        gname = self.gname
        geometry = mesh_store.get_mesh(gname, self.filename)
        if geometry is not None and not self.ghost:
            faces, vertices, vnormals = mesh_store.decompose_mesh(
                geometry, edit=False, copy=False, tri=True, transform=True
            )
            return faces, vertices, vnormals
        return [], [], []

    def setMesh(self, filename=None, vertices=None, faces=None, vnormals=None, **kw):
        """
        Set the 3d mesh from the given filename or the given mesh data (v,f,n)

        @type  filename: string
        @param filename: the name of the input file
        @type  vertices: array
        @param vertices: mesh vertices or None
        @type  faces: array
        @param faces: mesh faces or None
        @type  vnormals: array
        @param vnormals: mesh vnormals or None
        """
        if vertices is None and filename is not None:
            self.faces, self.vertices, self.vnormals = self.getMesh(filename)
        else:
            self.vertices = vertices
            self.faces = faces
            self.vnormals = vnormals
        if "fnormals" in kw:
            self.fnormals = kw["fnormals"]
        self.mesh = None
        self.ref_obj = filename
        self.bb = self.getBoundingBox()
        v = numpy.array(self.vertices, "f")
        length = numpy.sqrt((v * v).sum(axis=1))
        self.encapsulating_radius = max(length)

    def saveGridToFile(self, f):
        """Save insidePoints and surfacePoints to file"""
        pickle.dump(self.insidePoints, f)
        pickle.dump(self.surfacePoints, f)
        pickle.dump(self.surfacePointsNormals, f)
        pickle.dump(self.surfacePointsCoords, f)

    def readGridFromFile(self, f):
        """read insidePoints and surfacePoints from file"""
        self.insidePoints = insidePoints = pickle.load(f)
        self.surfacePoints = surfacePoints = pickle.load(f)
        self.surfacePointsNormals = surfacePointsNormals = pickle.load(f)
        self.surfacePointsCoords = surfacePointsCoords = pickle.load(f)
        return surfacePoints, insidePoints, surfacePointsNormals, surfacePointsCoords

    def setNumber(self, num):
        """set compartment uniq id"""
        self.number = num

    def setInnerRecipe(self, recipe):
        """set the inner recipe that define the ingredient to pack inside"""
        assert self.number is not None
        assert isinstance(recipe, Recipe)
        self.innerRecipe = recipe
        self.innerRecipe.number = self.number
        recipe.compartment = self  # weakref.ref(self)
        for ingr in recipe.ingredients:
            ingr.compNum = -self.number
            if hasattr(ingr, "compMask"):
                if not ingr.compMask:
                    ingr.compMask = [ingr.compNum]

    def setSurfaceRecipe(self, recipe):
        """set the inner recipe that define the ingredient to pack at the surface"""
        assert self.number is not None
        assert isinstance(recipe, Recipe)
        self.surfaceRecipe = recipe
        self.surfaceRecipe.number = self.number
        recipe.compartment = self  # weakref.ref(self)
        for ingr in recipe.ingredients:
            ingr.compNum = self.number

    def getCenter(self):
        """get the center of the mesh (vertices barycenter)"""
        if self.center is None:
            coords = numpy.array(self.vertices)  # self.allAtoms.coords
            center = sum(coords) / (len(coords) * 1.0)
            center = list(center)
            for i in range(3):
                center[i] = round(center[i], 4)
            self.center = center

    def getRadius(self):
        """get the radius as the distance between vertices center and bottom left bounding box"""
        import math

        d = self.center - self.bb[0]
        s = numpy.sum(d * d)
        return math.sqrt(s)

    def getBoundingBox(self):
        """get the bounding box"""
        mini = numpy.min(self.vertices, 0)
        maxi = numpy.max(self.vertices, 0)
        xl, yl, zl = mini
        xr, yr, zr = maxi
        self.diag = vlen(vdiff((xr, yr, zr), (xl, yl, zl)))
        return (mini, maxi)

    def getSizeXYZ(self):
        """get the size per axe"""
        sizexyz = [0, 0, 0]
        for i in range(3):
            sizexyz[i] = self.bb[1][i] - self.bb[0][i]
        return sizexyz

    def checkPointInsideBB(self, pt3d, dist=None):
        """check if the given 3d coordinate is inside the compartment bounding box"""
        origin = numpy.array(self.bb[0])
        E = numpy.array(self.bb[1])
        P = numpy.array(pt3d)

        # a point is inside is  < min and > maxi etc..
        test1 = P < origin
        test2 = P > E
        if True in test1 or True in test2:
            # outside
            return False
        else:
            if dist is not None:
                d1 = P - origin
                s1 = numpy.sum(d1 * d1)
                d2 = E - P
                s2 = numpy.sum(d2 * d2)
                if s1 <= dist or s2 <= dist:
                    return False
            return True

    def inBox(self, box, spacing):
        """
        check if bounding box of this compartment fits inside the give box
        returns true or false and the extended bounding box if this compartment
        did not fit
        """
        if self.ghost:
            return False, None
        bb = self.bb

        xm, ym, zm = box[0]
        xM, yM, zM = box[1]
        # padding 50 shows problem
        padding = spacing / 2

        newBB = [box[0][:], box[1][:]]
        fits = True

        if not bb:
            return True, newBB

        if xm > bb[0][0] - padding:
            newBB[0][0] = bb[0][0] - padding
            fits = False

        if ym > bb[0][1] - padding:
            newBB[0][1] = bb[0][1] - padding
            fits = False

        if zm > bb[0][2] - padding:
            newBB[0][2] = bb[0][2] - padding
            fits = False

        if xM < bb[1][0] + padding:
            newBB[1][0] = bb[1][0] + padding
            fits = False

        if yM < bb[1][1] + padding:
            newBB[1][1] = bb[1][1] + padding
            fits = False

        if zM < bb[1][2] + padding:
            newBB[1][2] = bb[1][2] + padding
            fits = False

        return fits, newBB

    def inGrid(self, point, fillBB):
        """
        check if bounding box of this compartment fits inside the give box
        returns true or false and the extended bounding box if this compartment
        did not fit
        """
        mini, maxi = fillBB
        mx, my, mz = mini
        Mx, My, Mz = maxi
        x, y, z = point
        if x >= mx and x <= Mx and y >= my and y <= My and z >= mz and z <= Mz:
            return True
        else:
            return False

    def get_normal_for_point(self, pt, pos, mesh_store):
        if pt not in self.surfacePointsNormals:
            normal = mesh_store.get_normal(self.gname, pos)
            self.surfacePointsNormals[pt] = normal
            return normal
        else:
            return self.surfacePointsNormals[pt]

    def getMinMaxProteinSize(self):
        """retrieve minimum and maximum ingredient size for inner and surface recipe ingredients"""
        # for compartment in self.compartments:
        #    mini, maxi = compartment.getSmallestProteinSize(size)
        mini1 = mini2 = 9999999.0
        maxi1 = maxi2 = 0.0
        if self.surfaceRecipe:
            mini1, maxi1 = self.surfaceRecipe.getMinMaxProteinSize()
        if self.innerRecipe:
            mini2, maxi2 = self.innerRecipe.getMinMaxProteinSize()
        return min(mini1, mini2), max(maxi1, maxi2)

    def getVertexNormals(self, vertices, faces):
        vnormals = vertices[:]
        face = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        v = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        for f in faces:
            for i in range(3):
                face[i] = vertices[f[i]]
            for i in range(3):
                v[0][i] = face[1][i] - face[0][i]
                v[1][i] = face[2][i] - face[0][i]
            normal = vcross(v[0], v[1])
            n = vlen(normal)
            if n == 0.0:
                n1 = 1.0
            else:
                n1 = 1.0 / n
            for i in range(3):
                vnormals[f[i]] = [normal[0] * n1, normal[1] * n1, normal[2] * n1]
        return vnormals  # areas added by Graham

    def getFaceNormals(self, vertices, faces, fillBB=None):
        """compute the face normal of the compartment mesh"""
        normals = []
        areas = []  # added by Graham
        face = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        v = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        for f in faces:
            for i in range(3):
                face[i] = vertices[f[i]]
            for i in range(3):
                v[0][i] = face[1][i] - face[0][i]
                v[1][i] = face[2][i] - face[0][i]
            normal = vcross(v[0], v[1])
            n = vlen(normal)
            if n == 0.0:
                n1 = 1.0
            else:
                n1 = 1.0 / n
            normals.append((normal[0] * n1, normal[1] * n1, normal[2] * n1))
            if fillBB is not None:
                if (
                    self.inGrid(vertices[f[0]], fillBB)
                    and self.inGrid(vertices[f[0]], fillBB)
                    and self.inGrid(vertices[f[0]], fillBB)
                ):
                    areas.append(0.5 * vlen(normal))  # added by Graham
        self.area = sum(areas)
        return normals, areas  # areas added by Graham

    def getInterpolatedNormal(self, pt, tri):
        """compute an interpolated normal for te given triangle at the given point"""
        v1, v2, v3 = self.faces[tri]
        verts = self.vertices
        d1 = vlen(vdiff(pt, verts[v1]))
        d2 = vlen(vdiff(pt, verts[v2]))
        d3 = vlen(vdiff(pt, verts[v3]))
        sumlen1 = d1 + d2 + d3
        w1 = sumlen1 / d1
        w2 = sumlen1 / d2
        w3 = sumlen1 / d3
        n1 = self.vnormals[v1]
        n2 = self.vnormals[v2]
        n3 = self.vnormals[v3]
        norm = (
            (n1[0] * w1 + n2[0] * w2 + n3[0] * w3),
            (n1[1] * w1 + n2[1] * w2 + n3[1] * w3),
            (n1[2] * w1 + n2[2] * w2 + n3[2] * w3),
        )
        l1 = 1.0 / vlen(norm)
        return (norm[0] * l1, norm[1] * l1, norm[2] * l1)

    def createSurfacePoints(self, maxl=20):
        """
        create points inside edges and faces with max distance between then maxl
        creates self.surfacePoints and self.surfacePointsNormals
        """
        vertices = self.vertices
        faces = self.faces
        vnormals = self.vnormals

        points = list(vertices)[:]
        normals = list(vnormals)[:]

        # create points in edges
        edges = {}
        for fn, tri in enumerate(faces):
            s1, s2 = tri[0], tri[1]
            if (s2, s1) in edges:
                edges[(s2, s1)].append(fn)
            else:
                edges[(s1, s2)] = [fn]

            s1, s2 = tri[1], tri[2]
            if (s2, s1) in edges:
                edges[(s2, s1)].append(fn)
            else:
                edges[(s1, s2)] = [fn]

            s1, s2 = tri[2], tri[0]
            if (s2, s1) in edges:
                edges[(s2, s1)].append(fn)
            else:
                edges[(s1, s2)] = [fn]

        for edge, faceInd in list(edges.items()):
            s1, s2 = edge
            p1 = vertices[s1]
            p2 = vertices[s2]
            v1 = vdiff(p2, p1)  # p1->p2
            l1 = vlen(v1)
            if l1 <= maxl:
                continue

            # compute number of points
            nbp1 = int(l1 / maxl)
            if nbp1 < 1:
                continue

            # compute interval size to spread the points
            dl1 = l1 / (nbp1 + 1)

            # compute interval vector
            dx1 = dl1 * v1[0] / l1
            dy1 = dl1 * v1[1] / l1
            dz1 = dl1 * v1[2] / l1
            x, y, z = p1
            nx1, ny1, nz1 = vnormals[s1]
            nx2, ny2, nz2 = vnormals[s2]
            edgeNorm = ((nx1 + nx2) * 0.5, (ny1 + ny2) * 0.5, (nz1 + nz2) * 0.5)
            for i in range(1, nbp1 + 1):
                points.append((x + i * dx1, y + i * dy1, z + i * dz1))
                normals.append(edgeNorm)

        for fn, t in enumerate(faces):
            # if t[0]==16 and t[1]==6 and t[2]==11:
            #    pdb.set_trace()
            pa = vertices[t[0]]
            pb = vertices[t[1]]
            pc = vertices[t[2]]

            va = vdiff(pb, pa)  # p1->p2
            la = vlen(va)
            if la <= maxl:
                continue

            vb = vdiff(pc, pb)  # p2->p3
            lb = vlen(vb)
            if lb <= maxl:
                continue

            vc = vdiff(pa, pc)  # p3->p1
            lc = vlen(vc)
            if lc <= maxl:
                continue

            # pick shortest edge to be second vector
            if la <= lb and la <= lc:
                p1 = pc
                p2 = pa
                v1 = vc
                l1 = lc
                v2 = va
                l2 = la
                v3 = vb

            if lb <= la and lb <= lc:
                p1 = pa
                p2 = pb
                v1 = va
                l1 = la
                v2 = vb
                l2 = lb
                v3 = vc

            if lc <= lb and lc <= la:
                p1 = pb
                p2 = pc
                v1 = vb
                l1 = lb
                v2 = vc
                l2 = lc
                v3 = va

            lengthRatio = l2 / l1

            nbp1 = int(l1 / maxl)
            if nbp1 < 1:
                continue

            dl1 = l1 / (nbp1 + 1)

            dx1 = dl1 * v1[0] / l1
            dy1 = dl1 * v1[1] / l1
            dz1 = dl1 * v1[2] / l1
            x, y, z = p1
            fn = vcross(v1, (-v3[0], -v3[1], -v3[2]))
            fnl = 1.0 / vlen(fn)
            faceNorm = (fn[0] * fnl, fn[1] * fnl, fn[2] * fnl)

            for i in range(1, nbp1 + 1):
                l2c = (i * dl1) * lengthRatio
                nbp2 = int(l2c / maxl)
                #                percentage = (i*dl1)/l1
                # nbp2 = int(l2*lengthRatio*percentage/maxl)
                if nbp2 < 1:
                    continue
                # dl2 = l2*percentage/(nbp2+1)
                dl2 = l2c / (nbp2 + 1)

                dx2 = dl2 * v2[0] / l2
                dy2 = dl2 * v2[1] / l2
                dz2 = dl2 * v2[2] / l2
                for j in range(1, nbp2 + 1):
                    points.append(
                        (
                            x + i * dx1 + j * dx2,
                            y + i * dy1 + j * dy2,
                            z + i * dz1 + j * dz2,
                        )
                    )
                    normals.append(faceNorm)

        self.ogsurfacePoints = points
        self.ogsurfacePointsNormals = normals

    def is_point_inside_mesh(self, point, diag, mesh_store, ray=1):
        insideBB = self.checkPointInsideBB(point)  # cutoff?
        if insideBB:
            return mesh_store.contains_point(self.gname, point)
        else:
            return False

    def BuildGrid(self, env, mesh_store=None):
        if self.is_orthogonal_bounding_box == 1:
            self.prepare_buildgrid_box(env)
        if self.ghost:
            return
        vertices = (
            self.vertices
        )  # NEED to make these limited to selection box, not whole compartment
        faces = (
            self.faces
        )  # Should be able to use self.ogsurfacePoints and collect faces too from above

        normalList2, areas = self.getFaceNormals(vertices, faces, fillBB=env.fillBB)
        vSurfaceArea = sum(areas)
        if self.is_box:
            self.overwriteSurfacePts = True
            self.BuildGrid_box(env, vSurfaceArea)
            return self.insidePoints, self.surfacePoints

        if self.overwriteSurfacePts:
            self.ogsurfacePoints = self.vertices[:]
            self.ogsurfacePointsNormals = self.vnormals[:]

        else:
            self.createSurfacePoints(maxl=env.grid.gridSpacing)
        # Graham Sum the SurfaceArea for each polyhedron
        distances = env.grid.distToClosestSurf
        compartment_ids = env.grid.compartment_ids
        diag = env.grid.diag
        self.log.info("distance %d", len(distances))

        # build search tree for off grid surface points
        off_grid_surface_points = self.ogsurfacePoints
        self.OGsrfPtsBht = ctree = spatial.cKDTree(
            tuple(off_grid_surface_points), leafsize=10
        )
        # res = numpy.zeros(len(srfPts),'f')
        # dist2 = numpy.zeros(len(srfPts),'f')

        master_grid_positions = env.grid.masterGridPositions
        new_distances, indexes = ctree.query(
            tuple(master_grid_positions)
        )  # return both indices and distances

        self.closestId = indexes
        # TODO: do this to the actual closest point on the mesh, not the closet vertex
        mask = distances[: len(master_grid_positions)] > new_distances
        grid_point_indexes = numpy.nonzero(mask)
        distances[grid_point_indexes] = new_distances[grid_point_indexes]

        if (
            env.innerGridMethod == "sdf" and self.is_orthogonal_bounding_box != 1
        ):  # A fillSelection can now be a mesh too... it can use either of these methods
            inside_points, surface_points = self.BuildGrid_utsdf(
                env
            )  # to make the outer most selection from the master and then the compartment
        elif env.innerGridMethod == "bhtree" and self.is_orthogonal_bounding_box != 1:
            inside_points, surface_points = self.BuildGrid_bhtree(
                env,
                ctree,
                master_grid_positions,
                diag,
                vSurfaceArea,
                off_grid_surface_points,
                compartment_ids,
                distances,
            )
        elif (
            env.innerGridMethod == "raytrace" and self.is_orthogonal_bounding_box != 1
        ):  # surfaces and interiors will be subtracted from it as normal!
            inside_points, surface_points = self.BuildGrid_ray(
                env,
                master_grid_positions,
                vSurfaceArea,
                off_grid_surface_points,
                compartment_ids,
                mesh_store,
            )
        elif (
            env.innerGridMethod == "pyray" and self.is_orthogonal_bounding_box != 1
        ):  # surfaces and interiors will be subtracted from it as normal!
            inside_points, surface_points = self.BuildGrid_pyray(
                env,
                ctree,
                distances,
                master_grid_positions,
                diag,
                vSurfaceArea,
                off_grid_surface_points,
                compartment_ids,
            )
        elif (
            env.innerGridMethod == "floodfill" and self.is_orthogonal_bounding_box != 1
        ):  # surfaces and interiors will be subtracted from it as normal!
            inside_points, surface_points = self.BuildGrid_kevin(env)
        elif (
            env.innerGridMethod == "binvox" and self.is_orthogonal_bounding_box != 1
        ):  # surfaces and interiors will be subtracted from it as normal!
            inside_points, surface_points = self.BuildGrid_binvox(
                env,
                master_grid_positions,
                vSurfaceArea,
                off_grid_surface_points,
                compartment_ids,
            )
        elif (
            env.innerGridMethod == "trimesh" and self.is_orthogonal_bounding_box != 1
        ):  # surfaces and interiors will be subtracted from it as normal!
            inside_points, surface_points = self.BuildGrid_trimesh(
                env,
                master_grid_positions,
                vSurfaceArea,
                off_grid_surface_points,
                compartment_ids,
                mesh_store,
            )
        elif (
            env.innerGridMethod == "scanline" and self.is_orthogonal_bounding_box != 1
        ):  # surfaces and interiors will be subtracted from it as normal!
            inside_points, surface_points = self.BuildGrid_scanline(
                env,
                master_grid_positions,
                new_distances,
                diag,
                vSurfaceArea,
                off_grid_surface_points,
                compartment_ids,
                mesh_store,
            )
        else:
            self.log.error("Not a recognized inner grid method", env.innerGridMethod)
        self.compute_volume_and_set_count(
            env, self.surfacePoints, self.insidePoints, areas=vSurfaceArea
        )
        return inside_points, surface_points

    def build_grid_sphere(self, env):
        grid_pts_in_sphere_indexes = env.grid.getPointsInSphere(
            self.position, self.radius
        )  # This is the highspeed shortcut for inside points! and no surface! that gets used if the fillSelection is an orthogonal box and there are no other compartments.
        env.grid.compartment_ids[grid_pts_in_sphere_indexes] = -self.number
        self.surfacePointsCoords = None
        vSurfaceArea = 4 * math.pi * self.radius**2
        self.log.info("vSurfaceArea = %r", vSurfaceArea)
        self.insidePoints = grid_pts_in_sphere_indexes
        self.surfacePoints = []
        self.surfacePointsCoords = []
        self.surfacePointsNormals = []
        self.log.info(
            f"{len(grid_pts_in_sphere_indexes)} inside pts, {len(grid_pts_in_sphere_indexes)} tot grid pts, {len(env.grid.masterGridPositions)} master grid"
        )

    def prepare_buildgrid_box(self, env):
        a = env.grid.getPointsInCube(
            self.bb, None, None
        )  # This is the highspeed shortcut for inside points! and no surface! that gets used if the fillSelection is an orthogonal box and there are no other compartments.
        env.grid.compartment_ids[a] = -self.number
        self.surfacePointsCoords = None
        bb0x, bb0y, bb0z = self.bb[0]
        bb1x, bb1y, bb1z = self.bb[1]
        AreaXplane = (bb1y - bb0y) * (bb1z - bb0z)
        AreaYplane = (bb1x - bb0x) * (bb1z - bb0z)
        AreaZplane = (bb1y - bb0y) * (bb1x - bb0x)
        vSurfaceArea = abs(AreaXplane) * 2 + abs(AreaYplane) * 2 + abs(AreaZplane) * 2
        self.log.info("vSurfaceArea = %r", vSurfaceArea)
        self.insidePoints = a
        self.surfacePoints = []
        self.surfacePointsCoords = []
        self.surfacePointsNormals = []
        self.log.info(
            "%d inside pts, %d tot grid pts, %d master grid",
            len(a),
            len(a),
            len(env.grid.masterGridPositions),
        )

    def BuildGrid_box(self, env, vSurfaceArea):
        nbGridPoints = len(env.grid.masterGridPositions)
        insidePoints = env.grid.getPointsInCube(self.bb, None, None, addSP=False)
        for p in insidePoints:
            env.grid.compartment_ids[p] = -self.number
        surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(
            self.ogsurfacePoints, env
        )
        srfPts = surfPtsBB
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints, srfPts, surfPtsBBNorms, env
        )
        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals
        self.log.info(
            "%s surface pts, %d inside pts, %d tot grid pts, %d master grid",
            len(self.surfacePoints),
            len(self.insidePoints),
            nbGridPoints,
            len(env.grid.masterGridPositions),
        )

    def BuildGrid_ray(
        self,
        env,
        grdPos,
        vSurfaceArea,
        vertex_points,
        idarray,
        mesh_store,
    ):
        """Build the compartment grid using pyembree raycast to find inside points,
        and then a cKDTree to find "missing" surface points, ie surface points that are
        in between vertex points"""

        insidePoints = []

        compartment_id = self.number
        spacing = env.grid.gridSpacing
        variation = self.encapsulating_radius - self.radius
        is_sphere = variation < spacing
        # now check if point inside
        tree = spatial.cKDTree(grdPos, leafsize=10)
        points_in_encap_sphere = tree.query_ball_point(
            self.center, self.encapsulating_radius, return_sorted=True
        )
        if is_sphere:
            inside = points_in_encap_sphere
        else:
            positions = grdPos[points_in_encap_sphere]
            inside = mesh_store.contains_points_slow(self.gname, positions)

        # set inside points in data
        inside_indexes = numpy.array(points_in_encap_sphere)[numpy.nonzero(inside)]
        insidePoints.extend(inside_indexes)
        idarray[inside_indexes] = -compartment_id

        # find missing surface points
        outside_points_positions = grdPos[numpy.nonzero(idarray != -compartment_id)]
        inside_points_positions = grdPos[inside_indexes]
        inside_tree = spatial.cKDTree(inside_points_positions, leafsize=10)
        surface_i = inside_tree.query_ball_point(
            outside_points_positions, env.grid.gridSpacing
        )
        on_grid_surface_point_positions = []
        for i in surface_i:
            if len(i) > 0:
                surface_indexes = inside_indexes[i]
                on_grid_surface_point_positions.extend(grdPos[surface_indexes])
                idarray[surface_indexes] = compartment_id
        number_of_base_grid_points = len(env.grid.masterGridPositions)
        (
            off_grid_surface_pt_pos,
            filtered_surface_pt_normals,
        ) = self.filter_surface_pts_to_fill_box(vertex_points, env)
        ex = True  # True if nbGridPoints == len(idarray) else False
        surface_point_ids, surfacePointsNormals = self.extendGridArrays(
            number_of_base_grid_points,
            off_grid_surface_pt_pos,
            filtered_surface_pt_normals,
            env,
            self.surfacePointsNormals,
            extended=ex,
        )
        all_surface_pt_pos = on_grid_surface_point_positions
        all_surface_pt_pos.extend(off_grid_surface_pt_pos)

        all_surface_pt_ids = numpy.nonzero(idarray == compartment_id)[0].tolist()
        all_surface_pt_ids.extend(surface_point_ids)
        self.insidePoints = insidePoints
        self.surfacePoints = all_surface_pt_ids
        self.surfacePointsCoords = all_surface_pt_pos
        self.surfacePointsNormals = surfacePointsNormals
        return self.insidePoints, self.surfacePoints

    def BuildGrid_binvox(self, env, grdPos, vSurfaceArea, srfPts, idarray, ray=1):
        # create surface points
        # check if file already exist, otherwise rebuild it
        number = self.number
        fileName = autopack.retrieve_file(self.filename, cache="geometries")
        filename, file_extension = os.path.splitext(fileName)
        binvox_filename = filename + ".binvox"
        bb = env.grid.boundingBox
        gridN = env.grid.nbGridPoints
        if not os.path.exists(binvox_filename):
            # build the file
            print("doesnt exist..build")
            # binvox.exe -c -d 30 -bb -850 -850 -850 850 850 850 HIV_VLP.dae
            os.system(
                autopack.binvox_exe
                + " -c -dc -d %i -bb %f %f %f %f %f %f %s\n"
                % (
                    gridN[0],
                    bb[0][0],
                    bb[0][1],
                    bb[0][2],
                    bb[1][0],
                    bb[1][1],
                    bb[1][2],
                    filename + ".obj",
                )
            )
        self.binvox_filename = binvox_filename
        # if use the exact approach, can do some floodfill after...
        with open(self.binvox_filename, "rb") as f:
            m, r = binvox_rw.read(f)
        with open(self.binvox_filename, "rb") as f:
            model = binvox_rw.read_as_coord_array(f)
        # model.translate=[0,0,0]
        model.axis_order = "xzy"
        # model.data = m.ijk.transpose()
        xyz_Data = model.ijkToxyz()
        self.binvox_model = m
        self.binvox_3d = xyz_Data

        # now check if point inside
        #
        # the main loop
        # need the ptInd that are inside the geom.
        m1 = (grdPos < bb[0]).any(axis=1)
        m2 = (grdPos > bb[1]).any(axis=1)
        m3 = m1 | m2
        # outside indice
        # outsidebb = np.nonzero(m3)[0]
        insidebb = numpy.nonzero(m3 is False)[0]

        ijk = numpy.rint(m.xyzToijk(grdPos[insidebb])).astype(int)
        i = m.ijkToIndex(ijk).astype(int)
        inbb_inside = numpy.nonzero(m.data[i] is True)[0]
        inside_points = insidebb[inbb_inside]
        idarray[inside_points] = -number
        nbGridPoints = len(env.grid.masterGridPositions)

        surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(srfPts, env)
        srfPts = surfPtsBB

        ex = True  # True if nbGridPoints == len(idarray) else False
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints, srfPts, surfPtsBBNorms, env, extended=ex
        )

        self.insidePoints = inside_points
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals

        return self.insidePoints, self.surfacePoints

    def BuildGrid_trimesh(self, env, grdPos, vSurfaceArea, srfPts, idarray, mesh_store):
        """Build the compartment grid ie surface and inside points"""
        insidePoints = []
        number = self.number
        # build trimer mesh
        mesh = mesh_store.get_mesh(self.gname)
        # voxelized
        self.log.info(f"{self.name}: CREATED MESH")
        trimesh_grid_surface = creation.voxelize(
            mesh, pitch=env.grid.gridSpacing / 2
        ).hollow()
        self.log.info("VOXELIZED MESH")
        # the main loop
        tree = spatial.cKDTree(grdPos, leafsize=10)
        points_in_encap_sphere = tree.query_ball_point(
            self.center,
            self.encapsulating_radius + env.grid.gridSpacing * 2,
            return_sorted=True,
        )
        self.log.info(f"GOT POINTS IN SPHERE {len(points_in_encap_sphere)}")
        for ptInd in points_in_encap_sphere:
            coord = [
                grdPos.item((ptInd, 0)),
                grdPos.item((ptInd, 1)),
                grdPos.item((ptInd, 2)),
            ]
            if idarray[ptInd] > 0:
                continue
            if trimesh_grid_surface.is_filled(coord):
                idarray.itemset(ptInd, number)
            elif mesh_store.contains_point(self.gname, coord):
                insidePoints.append(ptInd)
                idarray.itemset(ptInd, -number)
        self.log.info("ASSIGNED INSIDE OUTSIDE")

        nbGridPoints = len(env.grid.masterGridPositions)

        (
            surface_points_in_bounding_box,
            surfPtsBBNorms,
        ) = self.filter_surface_pts_to_fill_box(srfPts, env)
        srfPts = surface_points_in_bounding_box

        ex = True  # True if nbGridPoints == len(idarray) else False
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints,
            srfPts,
            surfPtsBBNorms,
            env,
            surfacePointsNormals=self.surfacePointsNormals,
            extended=ex,
        )

        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surface_points_in_bounding_box
        self.surfacePointsNormals = surfacePointsNormals
        return self.insidePoints, self.surfacePoints

    def BuildGrid_scanline(
        self,
        env,
        grdPos,
        new_distances,
        vSurfaceArea,
        diag,
        srfPts,
        idarray,
        mesh_store,
    ):
        """Build the compartment grid ie surface and inside point using scanline"""
        insidePoints = []
        number = self.number
        # the main loop
        # check the first point
        NX, NY, NZ = env.grid.nbGridPoints
        # int(k * NX * NY + j * NX + i)
        ptInd = 0
        coord = [
            grdPos.item((ptInd, 0)),
            grdPos.item((ptInd, 1)),
            grdPos.item((ptInd, 2)),
        ]
        # is this point inside
        inside = self.is_point_inside_mesh(coord, diag, mesh_store, ray=3)
        for k in range(NZ):
            for i in range(NX):
                for j in range(NY):
                    ptInd = int(k * NX * NY + j * NX + i)
                    coord = [
                        grdPos.item((ptInd, 0)),
                        grdPos.item((ptInd, 1)),
                        grdPos.item((ptInd, 2)),
                    ]
                    insideBB = self.checkPointInsideBB(
                        coord, dist=new_distances.item(ptInd)
                    )
                    if insideBB:
                        # check only if close enouhg to surface
                        if (
                            new_distances.item(ptInd)
                            < env.grid.gridSpacing * 1.1547 * 2.0
                        ):
                            inside = self.is_point_inside_mesh(
                                coord, diag, mesh_store, ray=3
                            )
                        if inside:
                            insidePoints.append(ptInd)
                            idarray.itemset(ptInd, -number)
                    p = (ptInd / float(len(grdPos))) * 100.0
                    if (ptInd % 1000) == 0 and autopack.verbose:
                        helper.progressBar(
                            progress=int(p),
                            label=str(ptInd)
                            + "/"
                            + str(len(grdPos))
                            + " inside "
                            + str(inside),
                        )
        nbGridPoints = len(env.grid.masterGridPositions)

        surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(srfPts, env)
        srfPts = surfPtsBB

        ex = True  # True if nbGridPoints == len(idarray) else False
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints,
            srfPts,
            surfPtsBBNorms,
            env,
            surfacePointsNormals=self.surfacePointsNormals,
            extended=ex,
        )

        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals
        return self.insidePoints, self.surfacePoints

    def BuildGrid_pyray(
        self, env, ctree, distances, grdPos, diag, vSurfaceArea, srfPts, idarray, ray=1
    ):

        if self.is_box:
            nbGridPoints = len(env.grid.masterGridPositions)
            insidePoints = env.grid.getPointsInCube(self.bb, None, None, addSP=False)
            for p in insidePoints:
                env.grid.compartment_ids[p] = -self.number
            surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(
                self.ogsurfacePoints, env
            )
            srfPts = surfPtsBB
            surfacePoints, surfacePointsNormals = self.extendGridArrays(
                nbGridPoints, srfPts, surfPtsBBNorms, env
            )
            self.insidePoints = insidePoints
            self.surfacePoints = surfacePoints
            self.surfacePointsCoords = surfPtsBB
            self.surfacePointsNormals = surfacePointsNormals
            return self.insidePoints, self.surfacePoints

        number = self.number
        insidePoints = []

        new_distance, nb = ctree.query(grdPos, 1)
        wh = numpy.greater(distances, new_distance)

        self.closestId = nb
        numpy.copyto(distances, new_distance, where=wh)
        # how to get closest triangle ? may help
        helper = autopack.helper
        geom = self.mesh
        center = helper.getCenter(srfPts)
        for ptInd in range(len(grdPos)):
            inside = False
            insideBB = self.checkPointInsideBB(grdPos[ptInd], dist=distances[ptInd])
            r = False
            if insideBB:
                # should use an optional direction for the ray, which will help for unclosed surface....
                intersect, count = helper.raycast(
                    geom,
                    grdPos[ptInd],
                    center,
                    diag,
                    count=True,
                    vertices=self.vertices,
                    faces=self.faces,
                )
                # intersect, count = helper.raycast(geom, grdPos[ptInd], grdPos[ptInd]+[0.,1.,0.], diag, count = True )
                r = (count % 2) == 1
                if ray == 3:
                    intersect2, count2 = helper.raycast(
                        geom,
                        grdPos[ptInd],
                        grdPos[ptInd] + [0.0, 0.0, 1.1],
                        diag,
                        count=True,
                        vertices=self.vertices,
                        faces=self.faces,
                    )
                    center = helper.rotatePoint(
                        helper.ToVec(center),
                        [0.0, 0.0, 0.0],
                        [1.0, 0.0, 0.0, math.radians(33.0)],
                    )
                    intersect3, count3 = helper.raycast(
                        geom,
                        grdPos[ptInd],
                        grdPos[ptInd] + [0.0, 1.1, 0.0],
                        diag,
                        count=True,
                        vertices=self.vertices,
                        faces=self.faces,
                    )
                    # intersect3, count3 = helper.raycast(geom, grdPos[ptInd], center, diag, count = True )#grdPos[ptInd]+[0.,1.1,0.]
                    if r:
                        if (count2 % 2) == 1 and (count3 % 2) == 1:
                            r = True
                        else:
                            r = False
            if r:  # odd inside
                inside = True
                if inside:
                    insidePoints.append(ptInd)
                    idarray[ptInd] = -number
            p = (ptInd / float(len(grdPos))) * 100.0

        nbGridPoints = len(env.grid.masterGridPositions)

        surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(srfPts, env)
        srfPts = surfPtsBB
        ex = True  # True if nbGridPoints == len(idarray) else False
        # back to list type
        # histoVol.grid.distToClosestSurf = histoVol.grid.distToClosestSurf.tolist()
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints, srfPts, surfPtsBBNorms, env, extended=ex
        )

        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals
        return self.insidePoints, self.surfacePoints

    def BuildGrid_bhtree(
        self,
        env,
        ctree,
        grdPos,
        new_distances,
        diag,
        vSurfaceArea,
        srfPts,
        idarray,
        distances,
    ):
        """Build the compartment grid ie surface and inside point"""
        res = numpy.zeros(len(srfPts), "f")
        dist2 = numpy.zeros(len(srfPts), "f")

        number = self.number
        ogNormals = self.ogsurfacePointsNormals
        insidePoints = []

        for ptInd in range(len(grdPos)):
            # find closest OGsurfacepoint
            gx, gy, gz = grdPos[ptInd]
            new_distance = new_distances[ptInd]
            if new_distances[ptInd] == -1:
                print("ouhoua, closest OGsurfacePoint = -1")
            if ptInd < len(srfPts):
                sx, sy, sz = srfPts[ptInd]
                d = math.sqrt(
                    (gx - sx) * (gx - sx)
                    + (gy - sy) * (gy - sy)
                    + (gz - sz) * (gz - sz)
                )
            else:
                try:
                    n = ctree.closePointsDist2(tuple(grdPos[ptInd]), diag, res, dist2)
                    d = min(dist2[0:n])
                    new_distance = res[tuple(dist2).index(d)]
                except Exception:
                    # this is quite long
                    delta = numpy.array(srfPts) - numpy.array(grdPos[ptInd])
                    delta *= delta
                    distA = numpy.sqrt(delta.sum(1))
                    d = min(distA)
                    new_distance = list(distA).index(d)
                sx, sy, sz = srfPts[new_distance]

            if distances[ptInd] > d:
                distances[ptInd] = d
            # case a diffent surface ends up being closer in the linear walk through the grid
            # check if ptInd in inside
            nx, ny, nz = [0, 0, 0]
            if ptInd < len(ogNormals):
                nx, ny, nz = numpy.array(ogNormals[ptInd])

            # check on what side of the surface point the grid point is
            vx, vy, vz = (gx - sx, gy - sy, gz - sz)
            dot = vx * nx + vy * ny + vz * nz
            if dot <= 0:  # inside
                # and the point is actually inside the mesh bounding box
                inside = True
                if self.checkinside:
                    inside = self.checkPointInsideBB(grdPos[ptInd], dist=d)
                # this is not working for a plane, or any unclosed compartment...
                if inside:
                    if (
                        ptInd < len(idarray) - 1
                    ):  # Oct 20, 2012 Graham asks: why do we do this if test? not in old code
                        idarray[ptInd] = -number
                    insidePoints.append(ptInd)
                #                if target2 is not None :
                #                    afvi.vi.changeObjColorMat(target2,[1,0,0])
                # sleep(0.01)
                #            c4d.StatusSetBar(int((ptInd/len(grdPos)*100)))

        nbGridPoints = len(env.grid.masterGridPositions)

        surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(srfPts, env)
        srfPts = surfPtsBB
        ex = True  # True if nbGridPoints == len(idarray) else False
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints,
            srfPts,
            surfPtsBBNorms,
            env,
            self.surfacePointsNormals,
            extended=ex,
        )
        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals

        return self.insidePoints, self.surfacePoints

    def BuildGrid_kevin(self, env, superFine=False):
        """Build the compartment grid ie surface and inside point using flood filling algo from kevin"""

        # Graham Sum the SurfaceArea for each polyhedron
        vertices = self.vertices[
            :
        ]  # NEED to make these limited to selection box, not whole compartment
        faces = self.faces[
            :
        ]  # Should be able to use self.ogsurfacePoints and collect faces too from above
        normalList2, areas = self.getFaceNormals(vertices, faces, fillBB=env.fillBB)

        srfPts = self.ogsurfacePoints
        self.OGsrfPtsBht = spatial.cKDTree(tuple(srfPts), leafsize=10)
        # res = numpy.zeros(len(srfPts),'f')
        # dist2 = numpy.zeros(len(srfPts),'f')

        # ogNormals = self.ogsurfacePointsNormals
        insidePoints = []

        # find closest off grid surface point for each grid point
        # FIXME sould be diag of compartment BB inside fillBB
        grid_point_positions = env.grid.masterGridPositions
        gridPtsPerEdge = env.grid.nbGridPoints
        gridSpacing = env.grid.gridSpacing
        radius = gridSpacing
        boundingBox = env.grid.boundingBox
        grid = env.grid
        #        returnNullIfFail = 0

        helper = autopack.helper

        # Pre-allocates a gridPoint object for every single point we have in our grid.
        gridPoints = []
        i = 0
        for point in grid_point_positions:
            gridPoints.append(gridPoint(i, point, isPolyhedron=False))
            i += 1
        assert len(gridPoints) == len(grid_point_positions)

        # Make a precomputed cube of coordinates and corresponding distances
        distanceCube, distX, distY, distZ = makeMarchingCube(gridSpacing, radius)
        # Flatten and combine these arrays. This is easier to iterate over.
        distanceCubeF, distXF, distYF, distZF = (
            distanceCube.flatten(),
            distX.flatten(),
            distY.flatten(),
            distZ.flatten(),
        )
        zippedNumbers = zip(distanceCubeF, distXF, distYF, distZF)

        NX, NY, NZ = gridPtsPerEdge
        OX, OY, OZ = boundingBox[0]
        spacing1 = (
            1.0 / gridSpacing
        )  # Inverse of the spacing. We compute this here, so we don't have to recompute it repeatedly
        allCoordinates = (
            []
        )  # Tracker for all the fine coordiantes that we have interpolated for the faces of the polyhedron
        # Walk through the faces, projecting each to the grid and marking immediate neighbors so we can test said
        # neighbors for inside/outside later.
        helper.progressBar(label="faces setup")
        for face in faces:
            # Get the vertex coordinates and convert to numpy arrays
            triCoords = [numpy.array(vertices[i]) for i in face]
            thisFaceFineCoords = list(triCoords)
            allCoordinates.extend(triCoords)
            # Use these u/v vectors to interpolate points that reside on the face
            pos = triCoords[0]
            u = triCoords[1] - pos
            v = triCoords[2] - pos
            # Smetimes the hypotenuse isn't fully represented, so use an additional w vector
            # to interpolate points on the hypotenuse
            w = triCoords[2] - triCoords[1]

            # If either u or v is greater than the grid spacing, then we need to subdivide it
            # We will use ceil: if we have a u of length 16, and grid spacing of 5, then we want
            # a u at 0, 5, 10, 15 which is [0, 1, 2, 3] * gridSpacing.

            # Using the default gridspacing, some faces will produce leakage. Instead, we
            # use a denser gridspacing to interpolate, and then project these points back our original spacing.
            # We'll decrease the gridspacing by 67% (so that it's 33% of the original). This seems be the
            # highest we can push this without leakage on edge cases.
            gridSpacingTempFine = gridSpacing / 3
            # Determine the number of grid spacing-sized points we can fit on each vector.
            # Minimum is one because range(1) gives us [0]
            uSubunits, vSubunits, wSubunits = 1, 1, 1
            if vlen(u) > gridSpacingTempFine:
                uSubunits = math.ceil(vlen(u) / gridSpacingTempFine) + 1
            if vlen(v) > gridSpacingTempFine:
                vSubunits = math.ceil(vlen(v) / gridSpacingTempFine) + 1
            if vlen(w) > gridSpacingTempFine:
                wSubunits = math.ceil(vlen(w) / gridSpacingTempFine) + 1
            # Because we have observed leakage, maybe we want to try trying a denser interpolation, using numpy's linspace?
            # Interpolate face of triangle into a fine mesh.
            for uSub in range(int(uSubunits)):
                percentU = uSub * gridSpacingTempFine / vlen(u)
                percentU = min(
                    percentU, 1.0
                )  # Make sure that we have not stepped outside of our original u vector
                # h represents the height of the hypotenuse at this u. Naturally, we cannot go past the hypotenuse, so this will be
                # our upper bound.
                h = percentU * u + (1 - percentU) * v
                for vSub in range(int(vSubunits)):
                    percentV = vSub * gridSpacingTempFine / vlen(v)
                    percentV = min(
                        percentV, 1.0
                    )  # Make sure that we have not stepped oustide of our original v vector.
                    interpolatedPoint = percentU * u + percentV * v
                    # The original if: statement asks if the distance from the origin to the interpolated point is less than
                    # the distance from the origin to the hypotenuse point, as such:
                    # if vlen(interpolatedPoint) < vlen(h):
                    # Wouldn't it be a better idea to measure distance to the u position instead? This is implemented below.
                    if vlen(interpolatedPoint - percentU * u) < vlen(h - percentU * u):
                        allCoordinates.append(interpolatedPoint + pos)
                        thisFaceFineCoords.append(interpolatedPoint + pos)
                    else:
                        break
            # Interpolate the hypotenuse of the triangle into a fine mesh. Prevents leakage.
            for wSub in range(int(wSubunits)):
                # Apply the same proceudre we did above for u/v, just for w (for hypotenuse interpolation)
                percentW = wSub * gridSpacingTempFine / vlen(w)
                percentW = min(percentW, 1.0)
                interpolatedPoint = percentW * w
                allCoordinates.append(interpolatedPoint + triCoords[1])
                thisFaceFineCoords.append(interpolatedPoint + triCoords[1])
            # Once we have interpolated the face, let's project each fine interpolated point to the grid.
            projectedIndices = set()
            for coord in thisFaceFineCoords:
                # Not sure if we need to flip the coordinates. Let's not flip them for now.
                projectedPointIndex = grid.getPointFrom3D(coord)
                projectedIndices.add(projectedPointIndex)

            # Walk through each grid point that our face spans, gather its closest neighbors, annotate them with
            # minimum distance and closest faces, & flag them for testing inside/outside later.
            for P in list(projectedIndices):
                # Get the point object corresponding to the index, and set its polyhedron attribute to true
                g = gridPoints[P]
                g.representsPolyhedron = True
                # Get the coordinates of the point, and convert them to grid units
                # Again, not sure if RH or LH coordinate system. Let's try RH for now.
                xTemp, yTemp, zTemp = g.globalCoord
                i, j, k = (
                    round((xTemp - OX) * spacing1),
                    round((yTemp - OY) * spacing1),
                    round((zTemp - OZ) * spacing1),
                )
                # Let's step through our distance cube, and assign faces/closest distances to each
                for d, x, y, z in zippedNumbers:
                    # Get the grid indices for the point we're considering, and pass if we're stepping oustide the boundaries
                    newI, newJ, newK = i + x, j + y, k + z
                    if (
                        newI < 0
                        or newI > (NX - 1)
                        or newJ < 0
                        or newJ > (NY - 1)
                        or newK < 0
                        or newK > (NZ - 1)
                    ):
                        continue
                    # Get the point index that this coordinate corresponds to.
                    desiredPointIndex = int(round(newK * NX * NY + newJ * NX + newI))
                    desiredPoint = gridPoints[desiredPointIndex]
                    if desiredPoint.representsPolyhedron:
                        continue
                    # Add the current face to the its list of closest faces
                    if face not in desiredPoint.closeFaces:
                        desiredPoint.closeFaces.append(face)
                    # Add the distance to the point's list of distances, and overwrite minimum distance if appropriate
                    desiredPoint.allDistances.append((v, d))
                    if d < desiredPoint.minDistance:
                        desiredPoint.minDistance = d
                        # Later down the road, we want to test as few points as possible for inside/outside. Therefore,
                        # we will only test points that are
                        # if abs(x) <= 1 and abs(y) <= 1 and abs(z) <= 1:
                        #     pointsToTestInsideOutside.add(desiredPointIndex)

        # Let's start flood filling in inside outside. Here's the general algorithm:
        # Walk through all the points in our grid. Once we encounter a point that has closest faces,
        # then we know we need to test it for inside/outside. Once we test that for inside/outside, we
        # fill in all previous points with that same inside outisde property. To account for the possible
        # situation that there is a surface that is only partially bound by the bbox, then we need to
        # reset the insideOutsideTracker every time we have a change in more than 1 of the 3 coordinates
        # because that indicates we're starting a new row/column of points.

        isOutsideTracker = None
        # This tracks the points that we've iterated over which we do not know if inside/outside.
        # Resets every time we find an inside/outside.
        emptyPointIndicies = []
        mismatchCounter = 0
        for g in gridPoints:
            # Check if we've started a new line. If so, then we reset everything.
            # This test should precede all other test, because we don't want old knowldge
            # to carry over to the new line, since we don't know if the polygon is only partially encapsulated by the bounding box.
            if g.index > 0:  # We can't check the first element, so we can skip it.
                coordDiff = g.globalCoord - gridPoints[g.index - 1].globalCoord
                coordDiffNonzero = [x != 0 for x in coordDiff]
                if sum(coordDiffNonzero) > 1:
                    # assert len(emptyPointIndicies) == 0 # When starting a new line, we shouldn't have any unknowns from the previous line
                    isOutsideTracker = None
                    emptyPointIndicies = []

            # There's no point testing inside/outside for points that are on the surface.
            if g.representsPolyhedron:
                g.isOutside = None
                continue

            if len(g.closeFaces) == 0:
                # If it's not close to any faces, and we don't know if this row is inside/outside, then
                # we have to wait till later to figure it out
                if isOutsideTracker is None:
                    emptyPointIndicies.append(g.index)
                # However, if we do know , we can just use the previous one to fill
                else:
                    g.isOutside = isOutsideTracker
                    # If there are close faces attached to it, then we need to test it for inside/outside.
            else:
                # Find centroid of all the vertices of all the close faces. This will be our endpoint
                # when casting a ray for collision testing.
                uniquePoints = []
                # This takes just the first face and projects to the center of it.
                # [uniquePoints.append(x) for x in g.closeFaces[0] if x not in uniquePoints]
                [
                    uniquePoints.append(x)
                    for x in g.closeFaces[g.closestFaceIndex]
                    if x not in uniquePoints
                ]
                uniquePointsCoords = vertices[uniquePoints]
                endPoint = findPointsCenter(uniquePointsCoords)
                g.testedEndpoint = endPoint

                # Draw a ray to that point, and see if we hit a backface or not
                numHits, thisBackFace = f_ray_intersect_polyhedron(
                    g.globalCoord, endPoint, g.closeFaces, vertices, False
                )

                # We can check the other face as well if we want to be super precise. If they dont' agree, we then check against the entire polyhedron.
                # We have not found any cases in which this is necessary, but it is included just in case.
                if superFine:
                    if len(g.closeFaces) > 1:
                        uniquePoints2 = []
                        [
                            uniquePoints2.append(x)
                            for x in g.closeFaces[1]
                            if x not in uniquePoints2
                        ]
                        uniquePointsCoords2 = vertices[uniquePoints2]
                        endPoint2 = findPointsCenter(uniquePointsCoords2)
                        numHits2, thisBackFace2 = f_ray_intersect_polyhedron(
                            g.globalCoord, endPoint2, g.closeFaces, vertices, False
                        )
                    if len(g.closeFaces) == 1 or thisBackFace != thisBackFace2:
                        mismatchCounter += 1
                        numHits, thisBackFace = f_ray_intersect_polyhedron(
                            g.globalCoord,
                            numpy.array([0.0, 0.0, 0.0]),
                            faces,
                            vertices,
                            False,
                        )

                # Fill in inside outside attribute for this point, as pRayStartPos, pRayEndPos, faces, vertices, pTruncateToSegmentll as for any points before it
                g.isOutside = not thisBackFace
                isOutsideTracker = not thisBackFace
                for i in emptyPointIndicies:
                    gridPoints[i].isOutside = isOutsideTracker
                # Because we have filled in all the unknowns, we can reset that counter.
                emptyPointIndicies = []
            if (g.index % 100) == 0:
                if autopack.verbose:
                    print(
                        str(g.index)
                        + "/"
                        + str(len(gridPoints))
                        + " inside "
                        + str(g.isOutside)
                    )

        # Final pass through for sanity checks.
        for g in gridPoints:
            if g.representsPolyhedron:
                assert g.isOutside is None
            else:
                if g.isOutside is None:
                    g.isOutside = True

        insidePoints = [g.globalCoord for g in gridPoints if g.isOutside is False]
        # outsidePoints = [g.index for g in gridPoints if g.isOutside == True]
        #        surfacePoints = [g.globalCoord for g in gridPoints if g.representsPolyhedron == True]

        nbGridPoints = len(env.grid.masterGridPositions)

        surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(
            self.ogsurfacePoints, env
        )
        srfPts = surfPtsBB

        ex = True  # True if nbGridPoints == len(idarray) else False
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints, srfPts, surfPtsBBNorms, env, extended=ex
        )
        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals
        return self.insidePoints, self.surfacePoints

    def extendGridArrays(
        self,
        nbGridPoints,
        off_grid_surface_pts,
        surfPtsBBNorms,
        env,
        surfacePointsNormals,
        extended=True,
    ):
        """Extend the environment grd using the compartment point"""
        if extended:
            number_off_grid_pts = len(off_grid_surface_pts)
            pointArrayRaw = numpy.zeros((nbGridPoints + number_off_grid_pts, 3), "f")
            pointArrayRaw[:nbGridPoints] = env.grid.masterGridPositions
            pointArrayRaw[nbGridPoints:] = off_grid_surface_pts
            env.grid.nbSurfacePoints += number_off_grid_pts
            env.grid.masterGridPositions = pointArrayRaw
            if type(env.grid.distToClosestSurf) == numpy.ndarray:
                # histoVol.grid.distToClosestSurf = numpy.append(histoVol.grid.distToClosestSurf,numpy.array([histoVol.grid.diag,]*length ))
                distCS = numpy.ones(number_off_grid_pts) * env.grid.diag
                env.grid.distToClosestSurf = numpy.hstack(
                    (env.grid.distToClosestSurf, distCS)
                )
            else:
                env.grid.distToClosestSurf.extend(
                    (numpy.ones(number_off_grid_pts) * env.grid.diag).tolist()
                )
            ptId = numpy.ones(number_off_grid_pts, "i") * self.number  # surface point
            env.grid.compartment_ids = numpy.hstack((env.grid.compartment_ids, ptId))
            env.grid.free_points = numpy.arange(nbGridPoints + number_off_grid_pts)
            surfacePoints = list(
                range(nbGridPoints, nbGridPoints + number_off_grid_pts)
            )
            for i, n in enumerate(surfPtsBBNorms):
                surfacePointsNormals[nbGridPoints + i] = n
        else:
            number_off_grid_pts = len(off_grid_surface_pts)
            pointArrayRaw = env.grid.masterGridPositions
            env.grid.nbSurfacePoints += number_off_grid_pts
            surfacePoints = list(
                range(nbGridPoints - number_off_grid_pts, nbGridPoints)
            )
            for i, n in enumerate(surfPtsBBNorms):
                surfacePointsNormals[nbGridPoints - number_off_grid_pts + i] = n
        return surfacePoints, surfacePointsNormals

    def filter_surface_pts_to_fill_box(self, off_grid_pos, env):
        """get the bounding box from the environment grid that encapsulated the mesh"""
        if self.highresVertices is not None:
            off_grid_pos = self.highresVertices
        surface_points_positions = []
        surfPtsBBNorms = []
        bottom_corner, top_corner = env.fillBB
        mx, my, mz = bottom_corner
        Mx, My, Mz = top_corner
        off_grid_normals = self.ogsurfacePointsNormals
        for i, p in enumerate(off_grid_pos):
            x, y, z = p
            if x >= mx and x <= Mx and y >= my and y <= My and z >= mz and z <= Mz:
                surface_points_positions.append(p)
                surfPtsBBNorms.append(off_grid_normals[i])
        return surface_points_positions, surfPtsBBNorms

    def BuildGridEnviroOnly(self, env, location=None):
        """Build the compartment grid ie surface and inside only environment"""
        # create surface points
        t1 = time()
        self.createSurfacePoints(maxl=env.grid.gridSpacing)

        # Graham Sum the SurfaceArea for each polyhedron
        vertices = (
            self.vertices
        )  # NEED to make these limited to selection box, not whole compartment
        faces = (
            self.faces
        )  # Should be able to use self.ogsurfacePoints and collect faces too from above
        normalList2, areas = self.getFaceNormals(vertices, faces, fillBB=env.fillBB)

        distances = env.grid.distToClosestSurf
        idarray = env.grid.compartment_ids
        #        diag = histoVol.grid.diag

        t1 = time()
        srfPts = self.ogsurfacePoints
        number = self.number
        ogNormals = self.ogsurfacePointsNormals
        insidePoints = []

        # find closest off grid surface point for each grid point
        # FIXME sould be diag of compartment BB inside fillBB
        grdPos = env.grid.masterGridPositions
        #        returnNullIfFail = 0
        closest = []  # bht.closestPointsArray(grdPos, diag, returnNullIfFail)

        def distanceLoop(
            ptInd,
            distances,
            grdPos,
            closest,
            srfPts,
            ogNormals,
            idarray,
            insidePoints,
            number,
        ):
            # find closest OGsurfacepoint
            gx, gy, gz = grdPos[ptInd]
            sptInd = closest[ptInd]
            # if closest[ptInd] == -1:
            #     pdb.set_trace()
            sx, sy, sz = srfPts[sptInd]

            # update distance field
            d = math.sqrt(
                (gx - sx) * (gx - sx) + (gy - sy) * (gy - sy) + (gz - sz) * (gz - sz)
            )
            if distances[ptInd] > d:
                distances[ptInd] = d

            # check if ptInd in inside
            nx, ny, nz = ogNormals[sptInd]
            # check on what side of the surface point the grid point is
            vx, vy, vz = (gx - sx, gy - sy, gz - sz)
            dot = vx * nx + vy * ny + vz * nz
            if dot < 0:  # inside
                idarray[ptInd] = -number
                insidePoints.append(ptInd)

        if location is None:
            [
                distanceLoop(
                    x,
                    distances,
                    grdPos,
                    closest,
                    srfPts,
                    ogNormals,
                    idarray,
                    insidePoints,
                    number,
                )
                for x in range(len(grdPos))
            ]
        else:
            insidePoints = list(range(len(grdPos)))
            for ptInd in range(len(grdPos)):
                distances[ptInd] = 99999.0
                idarray[ptInd] = location

        print("time to update distance field and idarray", time() - t1)

        t1 = time()
        nbGridPoints = len(env.grid.masterGridPositions)

        surfPtsBB = []
        surfPtsBBNorms = []
        mini, maxi = env.fillBB
        mx, my, mz = mini
        Mx, My, Mz = maxi
        ogNorms = self.ogsurfacePointsNormals
        for i, p in enumerate(srfPts):
            x, y, z = p
            if x >= mx and x <= Mx and y >= my and y <= My and z >= mz and z <= Mz:
                surfPtsBB.append(p)
                surfPtsBBNorms.append(ogNorms[i])

        self.log.info("surf points going from to %d %d", len(srfPts), len(surfPtsBB))
        srfPts = surfPtsBB
        length = len(srfPts)

        pointArrayRaw = numpy.zeros((nbGridPoints + length, 3), "f")
        pointArrayRaw[:nbGridPoints] = env.grid.masterGridPositions
        pointArrayRaw[nbGridPoints:] = srfPts
        self.surfacePointsCoords = srfPts
        env.grid.nbSurfacePoints += length
        env.grid.masterGridPositions = pointArrayRaw
        env.grid.distToClosestSurf.extend([env.grid.diag] * length)

        env.grid.compartment_ids.extend([number] * length)
        surfacePoints = list(range(nbGridPoints, nbGridPoints + length))
        env.grid.free_points.extend(surfacePoints)

        surfacePointsNormals = {}
        for i, n in enumerate(surfPtsBBNorms):
            surfacePointsNormals[nbGridPoints + i] = n

        insidePoints = insidePoints

        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals

        return self.insidePoints, self.surfacePoints

    def BuildGrid_utsdf(self, env):
        """
        Build the compartment grid ie surface and inside point using signed distance fields
        from the UT package
        """
        self.ogsurfacePoints = self.vertices[:]
        self.ogsurfacePointsNormals = self.vnormals[:]
        vertices = self.vertices
        faces = self.faces
        normalList2, areas = self.getFaceNormals(vertices, faces, fillBB=env.fillBB)
        #        labels = numpy.ones(len(faces), 'i')

        # FIXME .. dimensions on SDF should addapt to compartment size
        sizex = self.getSizeXYZ()

        from UTpackages.UTsdf import utsdf

        # can be 16,32,64,128,256,512,1024
        #        if spacing not in [16,32,64,128,256,512,1024]:
        #            spacing = self.find_nearest(numpy.array([16,32,64,128,256,512,1024]),spacing)
        # compute SDF
        dim = 16
        dim1 = dim + 1
        size = dim1 * dim1 * dim1

        verts = numpy.array(self.vertices, dtype="f")

        tris = numpy.array(self.faces, dtype="int")
        utsdf.setParameters(
            dim, 0, 1, [0, 0, 0, 0, 0, 0]
        )  # size, bool isNormalFlip, bool insideZero,bufferArr
        surfacePoints = srfPts = self.vertices
        datap = utsdf.computeSDF(
            numpy.ascontiguousarray(verts, dtype=numpy.float32),
            numpy.ascontiguousarray(tris, dtype=numpy.int32),
        )
        data = utsdf.createNumArr(datap, size)
        volarr = data[:]
        volarr.shape = (dim1, dim1, dim1)
        volarr = numpy.ascontiguousarray(numpy.transpose(volarr), "f")

        # get grid points distances to compartment surface
        from Volume.Operators.trilinterp import trilinterp

        invstep = (
            1.0 / (sizex[0] / dim),
            1.0 / (sizex[1] / dim),
            1.0 / (sizex[2] / dim),
        )
        origin = self.bb[0]
        distFromSurf = trilinterp(env.grid.masterGridPositions, volarr, invstep, origin)

        # update histoVol.distToClosestSurf
        distance = env.grid.distToClosestSurf
        for i, d in enumerate(distFromSurf):
            if distance[i] > d:
                distance[i] = d

        # loop over fill box grid points and build the idarray
        # identify inside and surface points and update the distance field
        insidePoints = []
        surfacePoints = []

        indice = numpy.nonzero(numpy.less(distance, 0.0))
        pointinside = numpy.take(env.grid.masterGridPositions, indice, 0)[0]
        if len(indice) == 1 and len(indice[0]) != 1:
            indice = indice[0]
        if len(pointinside) == 1 and len(pointinside[0]) != 1:
            pointinside = pointinside[0]
        env.grid.compartment_ids[indice] = -self.number
        nbGridPoints = len(env.grid.masterGridPositions)

        surfPtsBB, surfPtsBBNorms = self.filter_surface_pts_to_fill_box(srfPts, env)
        srfPts = surfPtsBB
        surfacePoints, surfacePointsNormals = self.extendGridArrays(
            nbGridPoints, srfPts, surfPtsBBNorms, env
        )

        insidePoints = pointinside

        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsCoords = surfPtsBB
        self.surfacePointsNormals = surfacePointsNormals
        return insidePoints, surfacePoints

    def get_bbox(self, vert_list, BB_SCALE=0.0):
        """get bounding box for the given list of vertices"""
        from multisdf import multisdf

        multisdf.cvar.BB_SCALE = BB_SCALE
        HUGE = 999999

        bbox = []
        x_min = HUGE
        x_max = -HUGE
        y_min = HUGE
        y_max = -HUGE
        z_min = HUGE
        z_max = -HUGE
        for i in range(len(vert_list)):
            p = vert_list[i]
            # check x-span
            if p[0] < x_min:
                x_min = p[0]
            if p[0] > x_max:
                x_max = p[0]
            # check y-span
            if p[1] < y_min:
                y_min = p[1]
            if p[1] > y_max:
                y_max = p[1]
            # check z-span
            if p[2] < z_min:
                z_min = p[2]
            if p[2] > z_max:
                z_max = p[2]

        bbox.append(x_min - BB_SCALE * (x_max - x_min))
        bbox.append(y_min - BB_SCALE * (y_max - y_min))
        bbox.append(z_min - BB_SCALE * (z_max - z_min))

        bbox.append(x_max + BB_SCALE * (x_max - x_min))
        bbox.append(y_max + BB_SCALE * (y_max - y_min))
        bbox.append(z_max + BB_SCALE * (z_max - z_min))
        return bbox

    def BuildGrid_multisdf(self, histoVol):
        """Build the compartment grid ie surface and inside point using multisdf"""
        vertices = self.vertices
        faces = self.faces
        labels = numpy.ones(len(faces), "i")

        # FIXME .. dimensions on SDF should addapt to compartment size
        bbox = self.get_bbox(vertices)
        xmin = bbox[0]
        ymin = bbox[1]
        zmin = bbox[2]
        xmax = bbox[3]
        ymax = bbox[4]
        zmax = bbox[5]

        # compute SDF
        from multisdf import multisdf

        gridSpacing = 30.0
        dimx = int((xmax - xmin) / gridSpacing) + 1
        dimy = int((ymax - ymin) / gridSpacing) + 1
        dimz = int((zmax - zmin) / gridSpacing) + 1

        gSizeX = (xmax - xmin) / (dimx - 1)
        gSizeY = (ymax - ymin) / (dimy - 1)
        gSizeZ = (zmax - zmin) / (dimz - 1)

        print("SDF grid size", dimx, dimy, dimz, gSizeX, gSizeY, gSizeZ)

        mind = -1000.0
        maxd = 1000.0
        datap = multisdf.computeSDF(
            vertices, faces, labels, dimx, dimy, dimz, maxd, mind
        )
        grid_size = dimx * dimy * dimz
        volarr = multisdf.createNumArr(datap, grid_size)
        volarr.shape = (dimz, dimy, dimx)
        volarr = numpy.ascontiguousarray(numpy.transpose(volarr), "f")

        # get grid points distances to compartment surface
        from Volume.Operators.trilinterp import trilinterp

        invstep = (1.0 / gridSpacing, 1.0 / gridSpacing, 1.0 / gridSpacing)
        origin = (xmin, ymin, zmin)
        distFromSurf = trilinterp(histoVol.masterGridPositions, volarr, invstep, origin)

        # save SDF
        self.sdfData = volarr
        self.sdfOrigin = origin
        self.sdfGridSpacing = (gSizeX, gSizeY, gSizeZ)
        self.sdfDims = (dimx, dimy, dimz)

        # update histoVol.distToClosestSurf
        distance = histoVol.distToClosestSurf
        for i, d in enumerate(distFromSurf):
            if distance[i] > d:
                distance[i] = d

        # loop over fill box grid points and build the idarray
        # identify inside and surface points and update the distance field
        number = self.number
        insidePoints = []
        surfacePoints = []
        allNormals = {}
        idarray = histoVol.compartment_ids
        # surfaceCutOff = histoVol.gridSpacing*.5
        # import pdb
        # pdb.set_trace()

        for i, d in enumerate(distance):

            # identify surface and interior points
            # there is a problem with SDF putting large negative values
            # for inside points. For now we pick all negative != mind as
            # surface points
            if d > 0:
                continue
            elif d < mind:
                surfacePoints.append(i)
                idarray[i] = number
                allNormals[i] = (1, 0, 0)
            else:
                insidePoints.append(i)
                idarray[i] = -number

        self.insidePoints = insidePoints
        self.surfacePoints = surfacePoints
        self.surfacePointsNormals = allNormals

        return insidePoints, surfacePoints

    def getSurfacePoint(self, p1, p2, w1, w2):
        """compute point between p1 and p2 with weight w1 and w2"""
        x1, y1, z1 = p1
        x2, y2, z2 = p2
        #        totalWeight = w1+w2
        ratio = w1 / (w1 + w2)
        vec = (x2 - x1, y2 - y1, z2 - z1)
        return x1 + ratio * vec[0], y1 + ratio * vec[1], z1 + ratio * vec[2]

    def compute_volume_and_set_count(
        self, env, surfacePoints, insidePoints, areas=None
    ):
        """
        Compute volume of surface and interior
        set 'count' in each ingredient of both recipes
        """
        unitVol = env.grid.gridSpacing**3
        if surfacePoints:
            self.log.info("%d surface points %.2f unitVol", len(surfacePoints), unitVol)
            # FIXME .. should be surface per surface point instead of unitVol
            self.surfaceVolume = len(surfacePoints) * unitVol
        if areas is not None:
            self.surfaceVolume = areas
        self.interiorVolume = len(insidePoints) * unitVol
        if self.surfaceVolume is not None:
            self.log.info(
                "%d surface volume %.2f interior volume",
                self.surfaceVolume,
                self.interiorVolume,
            )
        self.log.info(
            f"{self.name}: {self.interiorVolume} interior volume",
        )
        self.setCount()

    def setCount(self):
        # compute number of molecules and save in recipes
        rs = self.surfaceRecipe
        if rs:
            volume = self.surfaceVolume
            rs.setCount(volume)

        ri = self.innerRecipe
        if ri:
            volume = self.interiorVolume
            ri.setCount(volume)

    def getFacesNfromV(self, vindice, ext=0):
        """
        Retrieve the face normal from the indice of a vertice
        """
        f = []
        for i, af in enumerate(self.faces):
            if vindice in af:
                if ext:
                    for vi in af:
                        if vi != vindice:
                            ff = self.getFacesNfromV(vi)
                            f.extend(ff)
                else:
                    f.append(self.fnormals[i])
        return f

    def getVNfromF(self, i):
        """
        Retrieve the vertice normal from the indice of a vertice
        """
        self.normals
        fi = []
        for k, af in enumerate(self.faces):
            if i in af:
                for j in af:
                    if j not in fi:
                        fi.append(j)
        n = []
        for ind in fi:
            n.append(self.normals[ind])
        return n

    def create3DPointLookup(self, nbGridPoints, gridSpacing, dim, boundingBox=None):
        """
        Fill the orthogonal bounding box described by two global corners
         with an array of points spaces pGridSpacing apart. Duplicate from grid class
        """
        if boundingBox is None:
            boundingBox = self.bb
        xl, yl, zl = boundingBox[0]
        xr, yr, zr = boundingBox[1]

        nx, ny, nz = nbGridPoints
        pointArrayRaw = numpy.zeros((nx * ny * nz, 3), "f")
        ijkPtIndice = numpy.zeros((nx * ny * nz, 3), "i")
        size = self.getSizeXYZ()
        # Vector for lower left broken into real of only the z coord.
        i = 0
        for zi in range(nz):
            for yi in range(ny):
                for xi in range(nx):
                    pointArrayRaw[i] = (
                        xl + xi * (size[0] / dim),
                        yl + yi * (size[1] / dim),
                        zl + zi * (size[2] / dim),
                    )
                    ijkPtIndice[i] = (xi, yi, zi)
                    i += 1
        return ijkPtIndice, pointArrayRaw

    def find_nearest(self, array, value):
        """find nearest point indice of value in array using numpy"""
        idx = (numpy.abs(array - value)).argmin()
        return array[idx]

    # TOD add and store the grid_distances  (closest distance for each point). not only inside / outside

    def getSurfaceInnerPoints_sdf(
        self, boundingBox, spacing, display=True, useFix=False
    ):
        """
        Only compute the inner point. No grid.
        This is independant from the packing. Help build ingredient sphere tree and representation
        """
        from autopack.Environment import Grid

        self.grid = grid = Grid()
        grid.boundingBox = boundingBox
        grid.gridSpacing = spacing
        helper.progressBar(label="BuildGRid")
        grid.gridVolume, grid.nbGridPoints = grid.computeGridNumberOfPoint(
            boundingBox, spacing
        )
        grid.create3DPointLookup()
        nbPoints = grid.gridVolume
        grid.compartment_ids = [0] * nbPoints
        xl, yl, zl = boundingBox[0]
        xr, yr, zr = boundingBox[1]
        # distToClosestSurf is set to self.diag initially
        grid.diag = diag = vlen(vdiff((xr, yr, zr), (xl, yl, zl)))
        grid.distToClosestSurf = [diag] * nbPoints
        diag = grid.diag

        from UTpackages.UTsdf import utsdf

        # can be 16,32,64,128,256,512,1024
        if spacing not in [16, 32, 64, 128, 256, 512, 1024]:
            spacing = self.find_nearest(
                numpy.array([16, 32, 64, 128, 256, 512, 1024]), spacing
            )
        dim = spacing
        dim1 = dim + 1

        size = dim1 * dim1 * dim1
        # can be 16,32,64,128,256,512,1024
        verts = numpy.array(self.vertices, dtype="f")
        tris = numpy.array(self.faces, dtype="int")
        utsdf.setParameters(
            int(dim), 0, 1, [0, 0, 0, 0, 0, 0]
        )  # size, bool isNormalFlip, bool insideZero,bufferArr

        # spacing = length / 64
        sizes = self.getSizeXYZ()
        L = max(sizes)
        spacing = L / dim  # = self.smallestProteinSize*1.1547  # 2/sqrt(3)????
        # helper.progressBar(label="BuildGRid")
        # grid.gridVolume,grid.nbGridPoints = grid.computeGridNumberOfPoint(boundingBox,spacing)
        xl, yl, zl = boundingBox[0]
        xr, yr, zr = boundingBox[1]

        print("ok grid points")
        datap = utsdf.computeSDF(verts, tris)
        # datap = utsdf.computeSDF(verts,tris)
        print("ok computeSDF")
        data = utsdf.createNumArr(datap, size)
        self.grid_distances = data
        nbGridPoints = [dim1, dim1, dim1]
        ijkPtIndice, pointArrayRaw = self.create3DPointLookup(
            nbGridPoints, spacing, dim
        )
        print("ok grid", len(data), size)
        nbPoints = len(pointArrayRaw)
        print("n pts", nbPoints)
        grdPos = pointArrayRaw
        indice = numpy.nonzero(numpy.less(data, 0.0))
        pointinside = numpy.take(grdPos, indice, 0)
        # need to update the surface. need to create a aligned grid
        return pointinside[0], self.vertices

    def getSurfaceInnerPoints_kevin(
        self, boundingBox, spacing, display=True, superFine=False
    ):
        """
        Takes a polyhedron, and builds a grid. In this grid:
            - Projects the polyhedron to the grid.
            - Determines which points are inside/outside the polyhedron
            - Determines point's distance to the polyhedron.
        superFine provides the option doing a super leakproof test when determining
        which points are inside or outside. Instead of raycasting to nearby faces to
        determine inside/outside, setting this setting to true will foce the algorithm
        to raycast to the entire polyhedron. This usually not necessary, because the
        built-in algorithm has no known leakage cases, even in extreme edge cases.
        It is simply there as a safeguard.
        """
        # Start the timer.
        from time import time

        startTime = time()

        gridSpacing = spacing
        radius = gridSpacing

        # Make a copy of faces, vertices, and vnormals.
        faces = self.faces[:]
        vertices = self.vertices[:]
        from cellpack.autopack.Environment import Grid

        # Grid initialization
        self.grid = grid = Grid()  # setup=False)
        grid.boundingBox = boundingBox
        grid.gridSpacing = spacing
        grid.gridVolume, grid.nbGridPoints = grid.computeGridNumberOfPoint(
            boundingBox, spacing
        )
        grid.create3DPointLookup()
        points = grid.masterGridPositions
        gridPtsPerEdge = grid.nbGridPoints  # In the form [nx, ny, nz]

        # Pre-allocates a gridPoint object for every single point we have in our grid.
        # is this necessary ?
        gridPoints = []
        i = 0
        for point in points:
            gridPoints.append(gridPoint(i, point, isPolyhedron=False))
            i += 1
        assert len(gridPoints) == len(points)

        # Make a precomputed cube of coordinates and corresponding distances
        distanceCube, distX, distY, distZ = makeMarchingCube(gridSpacing, radius)
        # Flatten and combine these arrays. This is easier to iterate over.
        distanceCubeF, distXF, distYF, distZF = (
            distanceCube.flatten(),
            distX.flatten(),
            distY.flatten(),
            distZ.flatten(),
        )
        zippedNumbers = zip(distanceCubeF, distXF, distYF, distZF)

        NX, NY, NZ = gridPtsPerEdge
        OX, OY, OZ = boundingBox[0]
        spacing1 = (
            1.0 / gridSpacing
        )  # Inverse of the spacing. We compute this here, so we don't have to recompute it repeatedly
        allCoordinates = (
            []
        )  # Tracker for all the fine coordiantes that we have interpolated for the faces of the polyhedron

        # Walk through the faces, projecting each to the grid and marking immediate neighbors so we can test said
        # neighbors for inside/outside later.
        for face in faces:
            # Get the vertex coordinates and convert to numpy arrays
            triCoords = [numpy.array(vertices[i]) for i in face]
            thisFaceFineCoords = list(triCoords)
            allCoordinates.extend(triCoords)
            # Use these u/v vectors to interpolate points that reside on the face
            pos = triCoords[0]
            u = triCoords[1] - pos
            v = triCoords[2] - pos
            # Smetimes the hypotenuse isn't fully represented, so use an additional w vector
            # to interpolate points on the hypotenuse
            w = triCoords[2] - triCoords[1]

            # If either u or v is greater than the grid spacing, then we need to subdivide it
            # We will use ceil: if we have a u of length 16, and grid spacing of 5, then we want
            # a u at 0, 5, 10, 15 which is [0, 1, 2, 3] * gridSpacing.

            # Using the default gridspacing, some faces will produce leakage. Instead, we
            # use a denser gridspacing to interpolate, and then project these points back our original spacing.
            # We'll decrease the gridspacing by 67% (so that it's 33% of the original). This seems be the
            # highest we can push this without leakage on edge cases.
            gridSpacingTempFine = gridSpacing / 3
            # Determine the number of grid spacing-sized points we can fit on each vector.
            # Minimum is one because range(1) gives us [0]
            uSubunits, vSubunits, wSubunits = 1, 1, 1
            if vlen(u) > gridSpacingTempFine:
                uSubunits = math.ceil(vlen(u) / gridSpacingTempFine) + 1
            if vlen(v) > gridSpacingTempFine:
                vSubunits = math.ceil(vlen(v) / gridSpacingTempFine) + 1
            if vlen(w) > gridSpacingTempFine:
                wSubunits = math.ceil(vlen(w) / gridSpacingTempFine) + 1
            # Because we have observed leakage, maybe we want to try trying a denser interpolation, using numpy's linspace?
            # Interpolate face of triangle into a fine mesh.
            for uSub in range(uSubunits):
                percentU = uSub * gridSpacingTempFine / vlen(u)
                percentU = min(
                    percentU, 1.0
                )  # Make sure that we have not stepped outside of our original u vector
                # h represents the height of the hypotenuse at this u. Naturally, we cannot go past the hypotenuse, so this will be
                # our upper bound.
                h = percentU * u + (1 - percentU) * v
                for vSub in range(vSubunits):
                    percentV = vSub * gridSpacingTempFine / vlen(v)
                    percentV = min(
                        percentV, 1.0
                    )  # Make sure that we have not stepped oustide of our original v vector.
                    interpolatedPoint = percentU * u + percentV * v
                    # The original if: statement asks if the distance from the origin to the interpolated point is less than
                    # the distance from the origin to the hypotenuse point, as such:
                    # if vlen(interpolatedPoint) < vlen(h):
                    # Wouldn't it be a better idea to measure distance to the u position instead? This is implemented below.
                    if vlen(interpolatedPoint - percentU * u) < vlen(h - percentU * u):
                        allCoordinates.append(interpolatedPoint + pos)
                        thisFaceFineCoords.append(interpolatedPoint + pos)
                    else:
                        break
            # Interpolate the hypotenuse of the triangle into a fine mesh. Prevents leakage.
            for wSub in range(wSubunits):
                # Apply the same proceudre we did above for u/v, just for w (for hypotenuse interpolation)
                percentW = wSub * gridSpacingTempFine / vlen(w)
                percentW = min(percentW, 1.0)
                interpolatedPoint = percentW * w
                allCoordinates.append(interpolatedPoint + triCoords[1])
                thisFaceFineCoords.append(interpolatedPoint + triCoords[1])
            # Once we have interpolated the face, let's project each fine interpolated point to the grid.
            projectedIndices = set()
            for coord in thisFaceFineCoords:
                # Not sure if we need to flip the coordinates. Let's not flip them for now.
                projectedPointIndex = grid.getPointFrom3D(coord)
                projectedIndices.add(projectedPointIndex)

            # Walk through each grid point that our face spans, gather its closest neighbors, annotate them with
            # minimum distance and closest faces, & flag them for testing inside/outside later.
            for P in list(projectedIndices):
                # Get the point object corresponding to the index, and set its polyhedron attribute to true
                g = gridPoints[P]
                g.representsPolyhedron = True
                # Get the coordinates of the point, and convert them to grid units
                # Again, not sure if RH or LH coordinate system. Let's try RH for now.
                xTemp, yTemp, zTemp = g.globalCoord
                i, j, k = (
                    round((xTemp - OX) * spacing1),
                    round((yTemp - OY) * spacing1),
                    round((zTemp - OZ) * spacing1),
                )
                # Let's step through our distance cube, and assign faces/closest distances to each
                for d, x, y, z in zippedNumbers:
                    # Get the grid indices for the point we're considering, and pass if we're stepping oustide the boundaries
                    newI, newJ, newK = i + x, j + y, k + z
                    if (
                        newI < 0
                        or newI > (NX - 1)
                        or newJ < 0
                        or newJ > (NY - 1)
                        or newK < 0
                        or newK > (NZ - 1)
                    ):
                        continue
                    # Get the point index that this coordinate corresponds to.
                    desiredPointIndex = int(round(newK * NX * NY + newJ * NX + newI))
                    desiredPoint = gridPoints[desiredPointIndex]
                    if desiredPoint.representsPolyhedron:
                        continue
                    # Add the current face to the its list of closest faces
                    if face not in desiredPoint.closeFaces:
                        desiredPoint.closeFaces.append(face)
                    # Add the distance to the point's list of distances, and overwrite minimum distance if appropriate
                    desiredPoint.allDistances.append((v, d))
                    if d < desiredPoint.minDistance:
                        desiredPoint.minDistance = d
                        # Later down the road, we want to test as few points as possible for inside/outside. Therefore,
                        # we will only test points that are
                        # if abs(x) <= 1 and abs(y) <= 1 and abs(z) <= 1:
                        #     pointsToTestInsideOutside.add(desiredPointIndex)
        timeFinishProjection = time()
        print(
            "Projecting polyhedron to grid took "
            + str(timeFinishProjection - startTime)
            + " seconds."
        )

        # Let's start flood filling in inside outside. Here's the general algorithm:
        # Walk through all the points in our grid. Once we encounter a point that has closest faces,
        # then we know we need to test it for inside/outside. Once we test that for inside/outside, we
        # fill in all previous points with that same inside outisde property. To account for the possible
        # situation that there is a surface that is only partially bound by the bbox, then we need to
        # reset the insideOutsideTracker every time we have a change in more than 1 of the 3 coordinates
        # because that indicates we're starting a new row/column of points.

        isOutsideTracker = None
        # This tracks the points that we've iterated over which we do not know if inside/outside.
        # Resets every time we find an inside/outside.
        emptyPointIndicies = []
        mismatchCounter = 0
        for g in gridPoints:
            # Check if we've started a new line. If so, then we reset everything.
            # This test should precede all other test, because we don't want old knowldge
            # to carry over to the new line, since we don't know if the polygon is only partially encapsulated by the bounding box.
            if g.index > 0:  # We can't check the first element, so we can skip it.
                coordDiff = g.globalCoord - gridPoints[g.index - 1].globalCoord
                coordDiffNonzero = [x != 0 for x in coordDiff]
                if sum(coordDiffNonzero) > 1:
                    # assert len(emptyPointIndicies) == 0 # When starting a new line, we shouldn't have any unknowns from the previous line
                    isOutsideTracker = None
                    emptyPointIndicies = []

            # There's no point testing inside/outside for points that are on the surface.
            if g.representsPolyhedron:
                g.isOutside = None
                continue

            if len(g.closeFaces) == 0:
                # If it's not close to any faces, and we don't know if this row is inside/outside, then
                # we have to wait till later to figure it out
                if isOutsideTracker is None:
                    emptyPointIndicies.append(g.index)
                # However, if we do know , we can just use the previous one to fill
                else:
                    g.isOutside = isOutsideTracker
                    # If there are close faces attached to it, then we need to test it for inside/outside.
            else:
                # Find centroid of all the vertices of all the close faces. This will be our endpoint
                # when casting a ray for collision testing.
                uniquePoints = []
                # This takes just the first face and projects to the center of it.
                # [uniquePoints.append(x) for x in g.closeFaces[0] if x not in uniquePoints]
                [
                    uniquePoints.append(x)
                    for x in g.closeFaces[g.closestFaceIndex]
                    if x not in uniquePoints
                ]
                uniquePointsCoords = vertices[uniquePoints]
                endPoint = findPointsCenter(uniquePointsCoords)
                g.testedEndpoint = endPoint

                # Draw a ray to that point, and see if we hit a backface or not
                numHits, thisBackFace = f_ray_intersect_polyhedron(
                    g.globalCoord, endPoint, g.closeFaces, vertices, False
                )

                # We can check the other face as well if we want to be super precise. If they dont' agree, we then check against the entire polyhedron.
                # We have not found any cases in which this is necessary, but it is included just in case.
                if superFine:
                    if len(g.closeFaces) > 1:
                        uniquePoints2 = []
                        [
                            uniquePoints2.append(x)
                            for x in g.closeFaces[1]
                            if x not in uniquePoints2
                        ]
                        uniquePointsCoords2 = vertices[uniquePoints2]
                        endPoint2 = findPointsCenter(uniquePointsCoords2)
                        numHits2, thisBackFace2 = f_ray_intersect_polyhedron(
                            g.globalCoord, endPoint2, g.closeFaces, vertices, False
                        )
                    if len(g.closeFaces) == 1 or thisBackFace != thisBackFace2:
                        mismatchCounter += 1
                        numHits, thisBackFace = f_ray_intersect_polyhedron(
                            g.globalCoord,
                            numpy.array([0.0, 0.0, 0.0]),
                            faces,
                            vertices,
                            False,
                        )

                # Fill in inside outside attribute for this point, as pRayStartPos, pRayEndPos, faces, vertices, pTruncateToSegmentll as for any points before it
                g.isOutside = not thisBackFace
                isOutsideTracker = not thisBackFace
                for i in emptyPointIndicies:
                    gridPoints[i].isOutside = isOutsideTracker
                # Because we have filled in all the unknowns, we can reset that counter.
                emptyPointIndicies = []

        # Final pass through for sanity checks.
        for g in gridPoints:
            if g.representsPolyhedron:
                assert g.isOutside is None
            else:
                if g.isOutside is None:
                    g.isOutside = True
        print(
            "Flood filling grid inside/outside took "
            + str(time() - timeFinishProjection)
            + " seconds."
        )
        insidePoints = [g.globalCoord for g in gridPoints if not g.isOutside]
        # outsidePoints = [g.index for g in gridPoints if g.isOutside == True]
        surfacePoints = [g.globalCoord for g in gridPoints if g.representsPolyhedron]
        # distance ?
        if superFine:
            print(
                "Superfine was on and it identified "
                + str(mismatchCounter)
                + " mismatches."
            )
        print(
            "Grid construction took "
            + str(time() - startTime)
            + " seconds for "
            + str(len(faces))
            + " faces and "
            + str(len(gridPoints))
            + " points."
        )
        # what are the grid distance opinmt ?self.grid_distances
        return insidePoints, surfacePoints

    def getSurfaceInnerPoints_sdf_interpolate(
        self, boundingBox, spacing, display=True, useFix=False
    ):
        """
        Only compute the inner point. No grid.
        This is independant from the packing. Help build ingredient sphere tree and representation
        """
        from autopack.Environment import Grid

        self.grid = grid = Grid()
        grid.boundingBox = boundingBox
        grid.gridSpacing = spacing  # = self.smallestProteinSize*1.1547  # 2/sqrt(3)????
        helper.progressBar(label="BuildGRid")
        grid.gridVolume, grid.nbGridPoints = grid.computeGridNumberOfPoint(
            boundingBox, spacing
        )
        grid.create3DPointLookup()
        nbPoints = grid.gridVolume
        grid.compartment_ids = [0] * nbPoints
        xl, yl, zl = boundingBox[0]
        xr, yr, zr = boundingBox[1]
        # distToClosestSurf is set to self.diag initially
        grid.diag = diag = vlen(vdiff((xr, yr, zr), (xl, yl, zl)))
        grid.distToClosestSurf = [diag] * nbPoints
        diag = grid.diag
        dim = 16
        dim1 = dim + 1
        size = dim1 * dim1 * dim1
        from UTpackages.UTsdf import utsdf

        verts = numpy.array(self.vertices, dtype="f")
        tris = numpy.array(self.faces, dtype="int")
        utsdf.setParameters(
            dim, 0, 1, [0, 0, 0, 0, 0, 0]
        )  # size, bool isNormalFlip, bool insideZero,bufferArr
        print("ok grid points")
        # datap = utsdf.computeSDF(N.ascontiguousarray(verts, dtype=N.float32),N.ascontiguousarray(tris, dtype=N.int32))
        datap = utsdf.computeSDF(verts, tris)  # noncontiguous?
        print("ok computeSDF ", len(verts), len(tris))
        data = utsdf.createNumArr(datap, size)
        volarr = data[:]
        volarr.shape = (dim1, dim1, dim1)
        volarr = numpy.ascontiguousarray(numpy.transpose(volarr), "f")

        # get grid points distances to compartment surface
        from Volume.Operators.trilinterp import trilinterp

        sizex = self.getSizeXYZ()
        invstep = (
            1.0 / (sizex[0] / dim),
            1.0 / (sizex[0] / dim),
            1.0 / (sizex[0] / dim),
        )
        origin = self.bb[0]
        distFromSurf = trilinterp(grid.masterGridPositions, volarr, invstep, origin)
        # update histoVol.distToClosestSurf
        distance = grid.distToClosestSurf
        for i, d in enumerate(distFromSurf):
            if distance[i] > d:
                distance[i] = d
        self.grid_distances = distance
        #        idarray = histoVol.compartment_ids
        indice = numpy.nonzero(numpy.less(distance, 0.0))
        pointinside = numpy.take(grid.masterGridPositions, indice, 0)
        # need to update the surface. need to create a aligned grid
        return pointinside[0], self.vertices

    def getSurfaceInnerPoints(self, boundingBox, spacing, display=True, useFix=False):
        """
        Only compute the inner point. No grid.
        This is independant from the packing. Help build ingredient sphere tree and representation
        """
        from autopack.Environment import Grid

        self.grid = grid = Grid()
        grid.boundingBox = boundingBox
        grid.gridSpacing = spacing  # = self.smallestProteinSize*1.1547  # 2/sqrt(3)????
        helper.progressBar(label="BuildGRid")
        grid.gridVolume, grid.nbGridPoints = grid.computeGridNumberOfPoint(
            boundingBox, spacing
        )
        grid.create3DPointLookup()
        nbPoints = grid.gridVolume
        grid.compartment_ids = [0] * nbPoints
        xl, yl, zl = boundingBox[0]
        xr, yr, zr = boundingBox[1]
        # distToClosestSurf is set to self.diag initially
        grid.diag = diag = vlen(vdiff((xr, yr, zr), (xl, yl, zl)))
        grid.distToClosestSurf = [diag] * nbPoints
        distances = grid.distToClosestSurf
        idarray = grid.compartment_ids
        diag = grid.diag

        self.ogsurfacePoints = self.vertices[:]
        self.ogsurfacePointsNormals = self.vnormals[
            :
        ]  # helper.FixNormals(self.vertices,self.faces,self.vnormals,fn=self.fnormals)
        mat = helper.getTransformation(self.ref_obj)
        # c4dmat = poly.GetMg()
        # mat,imat = self.c4dMat2numpy(c4dmat)
        self.normals = helper.FixNormals(
            self.vertices, self.faces, self.vnormals, fn=self.fnormals
        )
        self.ogsurfacePointsNormals = helper.ApplyMatrix(
            numpy.array(self.normals), helper.ToMat(mat)
        )
        surfacePoints = srfPts = self.ogsurfacePoints
        self.OGsrfPtsBht = bht = spatial.cKDTree(tuple(srfPts), leafsize=10)

        res = numpy.zeros(len(srfPts), "f")
        dist2 = numpy.zeros(len(srfPts), "f")

        number = self.number
        ogNormals = numpy.array(self.ogsurfacePointsNormals)
        insidePoints = []

        # find closest off grid surface point for each grid point
        # FIXME sould be diag of compartment BB inside fillBB
        grdPos = grid.masterGridPositions
        returnNullIfFail = 0
        closest = bht.closestPointsArray(
            tuple(grdPos), diag, returnNullIfFail
        )  # diag is  cutoff ? meanin max distance ?

        self.closestId = closest
        t1 = time()
        helper.resetProgressBar()

        if display:
            sph = helper.Sphere("gPts", res=10, radius=20.0)[0]
            sph2 = helper.Sphere("sPts", res=10, radius=20.0)[0]
            cylN = helper.oneCylinder(
                "normal", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], radius=20.0
            )
            helper.oneCylinder("V", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], radius=20.0)
            helper.changeObjColorMat(sph2, (0.0, 0.0, 1.0))
        for ptInd in range(len(grdPos)):  # len(grdPos)):
            # find closest OGsurfacepoint
            if display:
                helper.changeObjColorMat(sph, (1.0, 1.0, 1.0))
                helper.changeObjColorMat(cylN, (1.0, 0.0, 0.0))
            inside = False
            gx, gy, gz = grdPos[ptInd]
            sptInd = closest[ptInd]  # this is a vertices
            if display:
                helper.setTranslation(sph, grdPos[ptInd])
                helper.setTranslation(sph2, srfPts[sptInd])
            #            helper.update()
            if closest[ptInd] == -1:
                print("ouhoua, closest OGsurfacePoint = -1")
                # pdb.set_trace()
                return
            if sptInd < len(srfPts):
                sx, sy, sz = srfPts[sptInd]
                d = math.sqrt(
                    (gx - sx) * (gx - sx)
                    + (gy - sy) * (gy - sy)
                    + (gz - sz) * (gz - sz)
                )
            else:
                #                try :
                n = bht.closePointsDist2(
                    tuple(grdPos[ptInd]), diag, res, dist2
                )  # wthis is not working
                d = min(dist2[0:n])
                sptInd = res[tuple(dist2).index(d)]
                sx, sy, sz = srfPts[sptInd]
            if distances[ptInd] > d:
                distances[ptInd] = d

            if self.fnormals is not None and useFix:
                # too slow
                facesN = self.getFacesNfromV(sptInd, ext=1)
                # now lets get all fnormals and averge them
                n = nx, ny, nz = numpy.average(numpy.array(facesN), 0)
            #            print (faces)

            # check if ptInd in inside
            else:
                n = nx, ny, nz = numpy.array(ogNormals[sptInd])
            #            vRayCollidePos = iRT.f_ray_intersect_polyhedron(numpy.array(grdPos[ptInd]), numpy.array(srfPts[sptInd]), self.ref_obj, 0,point = ptInd);
            #            if (vRayCollidePos %  2):
            #                print ("inside")
            #                inside = True
            #                idarray[ptInd] = -number
            #                insidePoints.append(grdPos[ptInd])
            #            vnpos = numpy.array(npost[sptInd])
            facesN = self.getVNfromF(sptInd)
            d1 = helper.measure_distance(
                numpy.array(grdPos[ptInd]), numpy.array(srfPts[sptInd]) + (n * 0.00001)
            )
            d2 = helper.measure_distance(
                numpy.array(grdPos[ptInd]), numpy.array(srfPts[sptInd])
            )
            print(
                "gridpont distance from surf normal %0.10f from surf  %0.10f closer to snormal %s"
                % (d1, d2, str(d1 < d2))
            )
            #             check on what side of the surface point the grid point is
            vptos = numpy.array(srfPts[sptInd]) - numpy.array(grdPos[ptInd])
            if display:
                #                helper.updateOneCylinder("normal",[0.,0.,0.],(n*spacing),radius=1.0)#srfPts[sptInd],numpy.array(srfPts[sptInd])+(n*spacing*10.0),radius=10.0)
                #                helper.updateOneCylinder("V",[0.,0,0.],vptos,radius=1.0)#srfPts[sptInd],numpy.array(srfPts[sptInd])+(v*spacing*10.0),radius=10.0)
                helper.updateOneCylinder(
                    "normal",
                    srfPts[sptInd],
                    numpy.array(srfPts[sptInd]) + (n * spacing * 10.0),
                    radius=10.0,
                )
                helper.updateOneCylinder(
                    "V",
                    srfPts[sptInd],
                    numpy.array(srfPts[sptInd]) + (vptos * spacing * 10.0),
                    radius=10.0,
                )
                helper.update()
            dots = []
            vptos = helper.normalize(vptos)
            for fn in facesN:
                dot = numpy.dot(vptos, fn)
                dots.append(dot)
                if display:
                    helper.updateOneCylinder(
                        "normal",
                        srfPts[sptInd],
                        numpy.array(srfPts[sptInd]) + (fn * spacing * 10.0),
                        radius=10.0,
                    )
                    helper.update()
            gr = numpy.greater(dots, 0.0)
            #            print dots
            #            print gr
            include = True
            if True in gr and False in gr:
                include = False
            dot = numpy.dot(vptos, n)  # project vptos on n -1 0 1
            vx, vy, vz = (gx - sx, gy - sy, gz - sz)
            dot2 = vx * nx + vy * ny + vz * nz
            a = helper.angle_between_vectors(vptos, n)
            if (
                dot > 0 and a < math.pi / 2.0 and include
            ):  # and d1 > d2 :#and dot < (-1.*10E-5): # inside ?
                print("INSIDE", dot, dot2, a, math.degrees(a))

                # and the point is actually inside the mesh bounding box
                inside = True
                # this is not working for a plane, or any unclosed compartment...
                if inside:
                    idarray[ptInd] = -number
                    insidePoints.append(grdPos[ptInd])
                if display:
                    helper.changeObjColorMat(sph, (1.0, 0.0, 0.0))
                    helper.update()
                    res = helper.drawQuestion(
                        title="Inside?",
                        question="%0.2f %0.2f %0.2f %0.2f %s"
                        % (d1, d2, a, math.degrees(a), str(inside)),
                    )
                    if not res:
                        return insidePoints, surfacePoints
                    #                sleep(5.0)

            p = (ptInd / float(len(grdPos))) * 100.0
            helper.progressBar(
                progress=int(p),
                label=str(ptInd) + "/" + str(len(grdPos)) + " inside " + str(inside),
            )

        print("total time", time() - t1)
        self.grid_distances = distances
        return insidePoints, surfacePoints

    def getSurfaceInnerPointsPandaRay(
        self, boundingBox, spacing, display=True, useFix=False
    ):
        """
        Only compute the inner point. No grid.
        This is independant from the packing. Help build ingredient sphere tree and representation
        """
        # should use the ray and see if it gave better reslt
        from autopack.pandautil import PandaUtil

        pud = PandaUtil()
        from autopack.Environment import Grid

        self.grid = grid = Grid()
        grid.boundingBox = boundingBox
        grid.gridSpacing = spacing  # = self.smallestProteinSize*1.1547  # 2/sqrt(3)????
        helper.progressBar(label="BuildGRid")
        grid.gridVolume, grid.nbGridPoints = grid.computeGridNumberOfPoint(
            boundingBox, spacing
        )
        grid.create3DPointLookup()
        nbPoints = grid.gridVolume
        grid.compartment_ids = [0] * nbPoints
        xl, yl, zl = boundingBox[0]
        xr, yr, zr = boundingBox[1]
        # distToClosestSurf is set to self.diag initially
        #        grid.diag = diag = vlen( vdiff((xr,yr,zr), (xl,yl,zl) ) )
        #        grid.distToClosestSurf = [diag]*nbPoints
        #        distances = grid.distToClosestSurf
        #        idarray = grid.compartment_ids
        #        diag = grid.diag
        grdPos = grid.masterGridPositions
        insidePoints = []
        surfacePoints = self.vertices
        pud.addMeshRB(self.vertices, self.faces)
        # then sed ray from pointgrid to closest surface oint and see if collide ?
        # distance ? dot ? angle
        grid.diag = diag = vlen(vdiff((xr, yr, zr), (xl, yl, zl)))
        grid.distToClosestSurf = [diag] * nbPoints
        idarray = grid.compartment_ids
        diag = grid.diag

        self.ogsurfacePoints = self.vertices[:]
        self.ogsurfacePointsNormals = helper.FixNormals(
            self.vertices, self.faces, self.vnormals, fn=self.fnormals
        )
        surfacePoints = srfPts = self.ogsurfacePoints
        self.OGsrfPtsBht = bht = spatial.cKDTree(tuple(srfPts), leafsize=10)

        res = numpy.zeros(len(srfPts), "f")

        number = self.number
        ogNormals = numpy.array(self.ogsurfacePointsNormals)
        insidePoints = []

        # find closest off grid surface point for each grid point
        # FIXME sould be diag of compartment BB inside fillBB
        grdPos = grid.masterGridPositions
        returnNullIfFail = 0
        closest = bht.closestPointsArray(
            tuple(grdPos), diag, returnNullIfFail
        )  # diag is  cutoff ? meanin max distance ?

        self.closestId = closest
        helper.resetProgressBar()
        #        helper.progressBar(label="checking point %d" % point)
        #       what abou intractive display ?
        if display:
            sph = helper.Sphere("gPts", res=10, radius=20.0)[0]
            sph2 = helper.Sphere("sPts", res=10, radius=20.0)[0]
            sph3 = helper.Sphere("hitPos", res=10, radius=20.0)[0]
            helper.oneCylinder("normal", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], radius=20.0)
            helper.oneCylinder("V", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], radius=20.0)
            helper.changeObjColorMat(sph2, (0.0, 0.0, 1.0))
        for ptInd in range(len(grdPos)):  # len(grdPos)):
            inside = False
            sptInd = closest[ptInd]
            v = -numpy.array(grdPos[ptInd]) + numpy.array(srfPts[closest[ptInd]])
            an = nx, ny, nz = numpy.array(ogNormals[sptInd])
            #            start = Point3(grdPos[i][0],grdPos[i][1],grdPos[i][2])
            if display:
                helper.setTranslation(sph, grdPos[ptInd])
                helper.setTranslation(sph2, srfPts[closest[ptInd]])
                helper.update()
            #            end = Point3(srfPts[closest[i]][0]*diag,srfPts[closest[i]][1]*diag,srfPts[closest[i]][2]*diag)
            # raycats and see what it it on the mesh
            # or result = world.sweepTestClosest(shape, tsFrom, tsTo, penetration)
            res = pud.rayCast(
                grdPos[ptInd], (numpy.array(grdPos[ptInd]) + v) * 99999, closest=True
            )  # world.rayTestAll(start, end)
            # can we get the number of hit?
            if res.hasHit():
                h = res
                #                hit=res.getHits()
                #                for h in hit :
                #                if len(hit):
                #                h = hit[0]
                n = numpy.array(h.getHitNormal())
                a = helper.angle_between_vectors(v, n)
                dot = numpy.dot(v, n)
                dot2 = numpy.dot(an, v)
                a2 = helper.angle_between_vectors(-v, an)
                print("hit with ", a, math.degrees(a), a2, math.degrees(a2), dot, dot2)
                if display:
                    helper.setTranslation(sph3, numpy.array(h.getHitPos()))
                    helper.updateOneCylinder(
                        "normal",
                        srfPts[sptInd],
                        numpy.array(srfPts[sptInd]) + (n * spacing * 10.0),
                        radius=10.0,
                    )
                    helper.updateOneCylinder(
                        "V",
                        grdPos[ptInd],
                        numpy.array(grdPos[ptInd]) + (v),
                        radius=10.0,
                    )
                    helper.update()
                #                    if dot < 0 :#and dot < (-1.*10E-5): # inside ?
                if (
                    dot < 0.0 and dot2 < 0.0
                ):  # a2 < (math.pi/2.)+0.1 and a > (math.pi/2.):# and a < (math.pi/2.) :#and a > (math.pi+(math.pi/2.)):
                    print("INSIDE", dot, a, math.degrees(a))
                    inside = True
                    if inside:
                        idarray[ptInd] = -number
                        insidePoints.append(grdPos[ptInd])
                    if display:
                        helper.changeObjColorMat(sph, (1.0, 0.0, 0.0))
                        helper.update()
                        res = helper.drawQuestion(
                            title="Inside?",
                            question="%0.2f %0.2f %0.2f %0.2f %s"
                            % (dot, dot2, a, math.degrees(a), str(inside)),
                        )
                        if not res:
                            return insidePoints, surfacePoints
            p = (ptInd / float(len(grdPos))) * 100.0
            helper.progressBar(
                progress=int(p),
                label=str(ptInd) + "/" + str(len(grdPos)) + " inside " + str(inside),
            )

        return insidePoints, surfacePoints

    def getSurfaceInnerPointsPanda(
        self, boundingBox, spacing, display=True, useFix=False
    ):
        """
        Only compute the inner point. No grid.
        This is independant from the packing. Help build ingredient sphere tree and representation
        """
        # work for small object
        from autopack.pandautil import PandaUtil

        pud = PandaUtil()
        from autopack.Environment import Grid

        self.grid = grid = Grid()
        grid.boundingBox = boundingBox
        grid.gridSpacing = spacing  # = self.smallestProteinSize*1.1547  # 2/sqrt(3)????
        t = time()
        helper.progressBar(label="BuildGRid")
        grid.gridVolume, grid.nbGridPoints = grid.computeGridNumberOfPoint(
            boundingBox, spacing
        )
        grid.create3DPointLookup()
        nbPoints = grid.gridVolume
        grid.compartment_ids = [0] * nbPoints
        xl, yl, zl = boundingBox[0]
        xr, yr, zr = boundingBox[1]
        # distToClosestSurf is set to self.diag initially
        #        grid.diag = diag = vlen( vdiff((xr,yr,zr), (xl,yl,zl) ) )
        #        grid.distToClosestSurf = [diag]*nbPoints
        #        distances = grid.distToClosestSurf
        #        idarray = grid.compartment_ids
        #        diag = grid.diag
        grdPos = grid.masterGridPositions
        insidePoints = []
        surfacePoints = self.vertices
        NPT = len(grdPos)
        rads = [spacing] * NPT
        helper.progressBar(label="BuildWorldAndNode")
        t = time()

        def addSphere(r, pos, i):
            node = pud.addSingleSphereRB(r, name=str(i))
            node.setPos(pos[0], pos[1], pos[2])
            helper.progressBar(
                progress=int((i / float(NPT)) * 100.0), label=str(i) + "/" + str(NPT)
            )
            return node

        [addSphere(rads[i], grdPos[i], i) for i in range(NPT)]
        #        node = pud.addMultiSphereRB(rads,grdPos)
        helper.progressBar(
            label="OK SPHERE %0.2f" % (time() - t)
        )  # ("time sphere ",time()-t)
        t = time()
        # add the mesh
        meshnode = pud.addMeshRB(self.vertices, self.faces)
        helper.progressBar(label="OK MESH %0.2f" % (time() - t))  #
        # computeCollisionTest
        t = time()
        iPtList = []
        meshcontacts = pud.world.contactTest(meshnode.node())
        meshcontacts.getNumContacts()
        for ct in meshcontacts.getContacts():
            i = eval(ct.getNode0().getName())
            if i not in iPtList:
                insidePoints.append(grdPos[i])
                iPtList.append(i)
        print("N", len(insidePoints), NPT)
        print("time contact", time() - t)
        return insidePoints, surfacePoints

    def printFillInfo(self):
        """print some info about the compartment and its recipe"""
        print("compartment %d" % self.number)
        r = self.surfaceRecipe
        if r is not None:
            print("    surface recipe:")
            r.printFillInfo("        ")

        r = self.innerRecipe
        if r is not None:
            print("    interior recipe:")
            r.printFillInfo("        ")
